import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import io
from datetime import datetime

# ============ CLASSE APPLI COMPTA / FEC ============

class ComptabiliteApp:
    def __init__(self):
        self.df = None               # toutes écritures FEC consolidées
        self.df_cumule_journalier = None  # CAHT/Cumul_TOTAL par jour déjà agrégé
        self.df_merged = None        # fusion FEC + externe

    def load_fec_files(self, uploaded_files):
        dfs = []
        for file in uploaded_files:
            df_tmp = pd.read_csv(
                file,
                sep="\t",
                dtype=str,
                encoding="utf-8",
                engine="python",
            )
            # nettoyage espaces
            df_tmp = df_tmp.apply(lambda col: col.str.strip() if col.dtype == "object" else col)
            dfs.append(df_tmp)

        self.df = pd.concat(dfs, ignore_index=True)

        # Conversion des champs critiques
        # Dates comptables
        if "EcritureDate" not in self.df.columns:
            st.error("Colonne 'EcritureDate' absente du FEC.")
            return

        self.df["EcritureDate"] = pd.to_datetime(
            self.df["EcritureDate"],
            format="%Y%m%d",
            errors="coerce"
        )

        # PieceDate (optionnel)
        if "PieceDate" in self.df.columns:
            self.df["PieceDate"] = pd.to_datetime(
                self.df["PieceDate"],
                format="%Y%m%d",
                errors="coerce"
            )

        # Montants (virgule -> point)
        for col_montant in ["Debit", "Credit", "Montantdevise"]:
            if col_montant in self.df.columns:
                self.df[col_montant] = (
                    self.df[col_montant]
                    .str.replace(",", ".", regex=False)
                    .str.replace(" ", "", regex=False)
                )
                self.df[col_montant] = pd.to_numeric(self.df[col_montant], errors="coerce")

        # CompteNum vers numérique tronqué
        if "CompteNum" in self.df.columns:
            self.df["CompteNum"] = self.df["CompteNum"].str[:8]
            self.df["CompteNum"] = pd.to_numeric(self.df["CompteNum"], errors="coerce")
        else:
            st.error("Colonne 'CompteNum' absente du FEC.")

        # Index temps propre
        self.df = self.df.sort_values("EcritureDate")
        self.df = self.df.set_index("EcritureDate")

    def compute_daily_total(self, start_compte, end_compte, start_date, end_date):
        """
        Calcule le 'Cumul_TOTAL' journalier (Crédit - Débit) sur la plage de dates
        et la plage de comptes demandées. Stocke le résultat dans self.df_cumule_journalier.
        """
        if self.df is None or self.df.empty:
            st.error("Aucun FEC chargé.")
            return None

        df_work = self.df.copy()

        # Filtrer comptes
        df_work = df_work[
            (df_work['CompteNum'] >= start_compte) &
            (df_work['CompteNum'] <= end_compte)
        ]

        # Sécurise les colonnes montants
        for col_montant in ["Debit", "Credit"]:
            df_work[col_montant] = (
                df_work[col_montant]
                .astype(str)
                .str.replace(",", ".", regex=False)
                .str.replace(" ", "", regex=False)
            )
            df_work[col_montant] = pd.to_numeric(df_work[col_montant], errors="coerce")

        # TOTAL = Crédit - Débit
        df_work['TOTAL'] = df_work['Credit'] - df_work['Debit']

        # Filtre période dates (sur l'index qui est déjà EcritureDate)
        df_period = df_work.loc[start_date:end_date]

        # Agrégat journalier
        df_cumule = (
            df_period
            .groupby(df_period.index)['TOTAL']
            .sum()
            .reset_index()
            .rename(columns={'EcritureDate': 'EcritureDate', 'TOTAL': 'Cumul_TOTAL'})
        )

        # ATTENTION : après reset_index(), la colonne date s'appelle par défaut "EcritureDate" ?
        # groupby(...).sum().reset_index() crée une colonne qui porte le nom de l'index d'origine.
        # Sur la ligne du rename() ci-dessus :
        # - df_cumule.columns[0] est la colonne date
        # - On veut un nom clair et stable "EcritureDate"
        df_cumule.columns = ["EcritureDate", "Cumul_TOTAL"]

        # On stocke pour réutilisation dans la fusion
        self.df_cumule_journalier = df_cumule.copy()

        return df_cumule

    def load_external_data(self, external_file):
        """
        Lit un fichier externe (Excel ou CSV) en gardant tout en chaîne, puis essaie de convertir.
        Retourne le df externe sans le fusionner.
        """
        if external_file is None:
            return None

        file_name = external_file.name.lower()

        if file_name.endswith(".csv"):
            df_ext = pd.read_csv(
                external_file,
                dtype=str,
                encoding="utf-8",
                engine="python",
                sep=None  # sep=None => sniff automatique ; tu peux imposer ';' si besoin
            )
        else:
            # On suppose Excel
            df_ext = pd.read_excel(
                external_file,
                dtype=str,
            )

        # strip espaces autour
        df_ext = df_ext.apply(lambda col: col.str.strip() if col.dtype == "object" else col)

        return df_ext

    def merge_on_dates(self, df_ext, external_date_col):
        """
        Fusionne self.df_cumule_journalier (CA par jour) avec df_ext (données météo, etc.)
        en ne gardant QUE les dates présentes dans le FEC.

        external_date_col = nom de la colonne date dans df_ext.
        """
        if self.df_cumule_journalier is None or self.df_cumule_journalier.empty:
            st.error("Le cumul journalier FEC n'a pas été calculé.")
            return None

        if df_ext is None or df_ext.empty:
            st.error("Pas de données externes chargées.")
            return None

        # 1. Convertir colonne date externe en datetime.date
        df_ext_local = df_ext.copy()

        # Essai 1 : format AAAA-MM-JJ
        # Essai 2 : format AAAAMMJJ (comme le FEC)
        # Sinon => to_datetime libre
        def to_date(s):
            # essaie AAAAMMJJ
            try:
                return pd.to_datetime(s, format="%Y%m%d", errors="raise").date()
            except Exception:
                pass
            # essaie ISO
            try:
                return pd.to_datetime(s, format="%Y-%m-%d", errors="raise").date()
            except Exception:
                pass
            # fallback auto
            try:
                return pd.to_datetime(s, errors="raise").date()
            except Exception:
                return pd.NaT

        df_ext_local[external_date_col] = df_ext_local[external_date_col].apply(to_date)

        # 2. Convertir la colonne date du FEC en datetime.date aussi
        df_fec_daily = self.df_cumule_journalier.copy()
        df_fec_daily["EcritureDate_only"] = df_fec_daily["EcritureDate"].dt.date

        # 3. aligner sur les dates FEC uniquement : left join
        merged = pd.merge(
            df_fec_daily,
            df_ext_local,
            left_on="EcritureDate_only",
            right_on=external_date_col,
            how="left"
        )

        # On n'a plus besoin de la colonne technique
        merged = merged.drop(columns=["EcritureDate_only", external_date_col], errors="ignore")

        self.df_merged = merged.copy()
        return merged


# ============ STREAMLIT APP ============

st.title("Analyse CA Journalier FEC + Données Externes (météo, fréquentation...)")

app = ComptabiliteApp()

# 1. Upload FEC
uploaded_fec_files = st.file_uploader(
    "Importe ton (ou tes) FEC (.txt)",
    type=["txt", "csv"],
    accept_multiple_files=True
)

# 2. Upload données externes
uploaded_external_file = st.file_uploader(
    "Importe les données externes (météo, trafic, etc.) (.xlsx / .xls / .csv)",
    type=["xlsx", "xls", "csv"],
    accept_multiple_files=False
)

if uploaded_fec_files:
    if len(uploaded_fec_files) > 6:
        st.warning("Vous ne pouvez importer que jusqu'à 6 fichiers FEC.")
    else:
        # Charger FEC
        app.load_fec_files(uploaded_fec_files)

        if app.df is not None and not app.df.empty:
            # bornes compte
            start_compte = st.number_input("Numéro de compte de début", min_value=0, value=70000000)
            end_compte   = st.number_input("Numéro de compte de fin", min_value=0, value=70999999)

            # plage de dates dispo dans le FEC
            start_date_default = app.df.index.min().date()
            end_date_default   = app.df.index.max().date()

            start_date_input = st.date_input("Date de début (FEC)", value=start_date_default)
            end_date_input   = st.date_input("Date de fin (FEC)",   value=end_date_default)

            if st.button("1️⃣ Calculer le CAHT / Cumul_TOTAL journalier"):
                df_daily = app.compute_daily_total(
                    start_compte,
                    end_compte,
                    start_date_input,
                    end_date_input
                )

                if df_daily is not None:
                    st.write("### CA / TOTAL par jour (issu du FEC)")
                    st.dataframe(df_daily)

                    # petit graphique direct
                    plt.figure(figsize=(14, 6))
                    plt.plot(df_daily['EcritureDate'], df_daily['Cumul_TOTAL'], marker='o', linestyle='-')
                    plt.xlabel('Date')
                    plt.ylabel('Cumul_TOTAL (Crédit - Débit)')
                    plt.title('Cumul TOTAL journalier (FEC)')
                    plt.grid(True)
                    plt.xticks(rotation=45)
                    plt.tight_layout()

                    img_buf = io.BytesIO()
                    plt.savefig(img_buf, format='png')
                    img_buf.seek(0)

                    st.image(img_buf)

        else:
            st.error("Le FEC semble vide ou illisible.")

        # --- PARTIE FUSION AVEC DONNÉES EXTERNES ---
        if uploaded_external_file and app.df_cumule_journalier is not None:
            st.markdown("---")
            st.subheader("Fusion avec la donnée externe")

            df_ext_preview = app.load_external_data(uploaded_external_file)
            if df_ext_preview is not None and not df_ext_preview.empty:
                st.write("Aperçu du fichier externe :")
                st.dataframe(df_ext_preview.head())

                # Choix de la colonne date dans le fichier externe
                possible_date_cols = list(df_ext_preview.columns)
                external_date_col = st.selectbox(
                    "Quelle colonne correspond à la date dans les données externes ?",
                    possible_date_cols
                )

                if st.button("2️⃣ Fusionner (left join sur les dates du FEC)"):
                    merged = app.merge_on_dates(df_ext_preview, external_date_col)

                    if merged is not None:
                        st.write("### Résultat FEC + Donnée externe")
                        st.dataframe(merged)

                        # Export Excel fusionné
                        excel_buf_merge = io.BytesIO()
                        merged.to_excel(excel_buf_merge, index=False)
                        excel_buf_merge.seek(0)

                        st.download_button(
                            label="Télécharger le fichier fusionné (Excel)",
                            data=excel_buf_merge,
                            file_name="FEC_avec_externe.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
