import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import io
from datetime import datetime

# -----------------------
# Utilitaires
# -----------------------

def lire_fec(uploaded_files):
    """Lit 1..6 fichiers FEC txt/csv tabulés, concatène, nettoie, convertit."""
    dfs = []
    for file in uploaded_files:
        df_tmp = pd.read_csv(
            file,
            sep="\t",
            dtype=str,
            encoding="utf-8",
            engine="python",
        )
        # strip espaces
        df_tmp = df_tmp.apply(lambda col: col.str.strip() if col.dtype == "object" else col)
        dfs.append(df_tmp)

    df = pd.concat(dfs, ignore_index=True)

    # --- EcritureDate
    if "EcritureDate" not in df.columns:
        st.error("Colonne 'EcritureDate' absente du FEC.")
        return None

    df["EcritureDate"] = pd.to_datetime(
        df["EcritureDate"],
        format="%Y%m%d",
        errors="coerce"
    )

    # --- PieceDate (optionnel)
    if "PieceDate" in df.columns:
        df["PieceDate"] = pd.to_datetime(
            df["PieceDate"],
            format="%Y%m%d",
            errors="coerce"
        )

    # --- Montants
    for col_montant in ["Debit", "Credit", "Montantdevise"]:
        if col_montant in df.columns:
            df[col_montant] = (
                df[col_montant]
                .str.replace(",", ".", regex=False)
                .str.replace(" ", "", regex=False)
            )
            df[col_montant] = pd.to_numeric(df[col_montant], errors="coerce")

    # --- CompteNum
    if "CompteNum" not in df.columns:
        st.error("Colonne 'CompteNum' absente du FEC.")
        return None

    df["CompteNum"] = df["CompteNum"].str[:8]
    df["CompteNum"] = pd.to_numeric(df["CompteNum"], errors="coerce")

    # Index temps
    df = df.sort_values("EcritureDate")
    df = df.set_index("EcritureDate")

    return df


def calc_ca_journalier_avec_trous(df_fec, start_compte, end_compte, start_date, end_date):
    """
    À partir du FEC indexé par EcritureDate :
    - filtre la plage de comptes
    - calcule TOTAL = Crédit - Débit
    - restreint à la plage de dates demandée
    - agrège par jour
    - génère toutes les dates de la plage et remplit les jours sans écriture avec 0
    Retourne un df_journalier avec colonnes [EcritureDate, Cumul_TOTAL]
    """

    # copie travail
    work = df_fec.copy()

    # filtre comptes
    work = work[
        (work["CompteNum"] >= start_compte) &
        (work["CompteNum"] <= end_compte)
    ]

    # sécurité montants
    for col_montant in ["Debit", "Credit"]:
        work[col_montant] = (
            work[col_montant]
            .astype(str)
            .str.replace(",", ".", regex=False)
            .str.replace(" ", "", regex=False)
        )
        work[col_montant] = pd.to_numeric(work[col_montant], errors="coerce")

    # TOTAL = Crédit - Débit
    work["TOTAL"] = work["Credit"] - work["Debit"]

    # filtre plage date
    work_period = work.loc[start_date:end_date]

    # agrégat journalier
    df_daily = (
        work_period
        .groupby(work_period.index)["TOTAL"]
        .sum()
        .reset_index()
    )
    df_daily.columns = ["EcritureDate", "Cumul_TOTAL"]

    # créer calendrier continu
    all_days = pd.date_range(start=start_date, end=end_date, freq="D")
    df_all = pd.DataFrame({"EcritureDate": all_days})

    # merge pour insérer les trous
    df_daily_full = pd.merge(df_all, df_daily, on="EcritureDate", how="left")

    # remplissage 0 pour les jours sans écriture
    df_daily_full["Cumul_TOTAL"] = df_daily_full["Cumul_TOTAL"].fillna(0)

    return df_daily_full


def plot_ca(df_daily_full):
    """Trace le CA quotidien."""
    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(
        df_daily_full["EcritureDate"],
        df_daily_full["Cumul_TOTAL"],
        marker="o",
        linestyle="-"
    )
    ax.set_xlabel("Date")
    ax.set_ylabel("Cumul_TOTAL (Crédit - Débit)")
    ax.set_title("Cumul TOTAL journalier (FEC)")
    ax.grid(True)
    plt.xticks(rotation=45)
    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    buf.seek(0)
    return buf


def lire_externe(file_ext):
    """
    Charge le fichier externe (xlsx/xls/csv) en string.
    Ne convertit pas encore la date -> on le fera après,
    quand l'utilisateur aura choisi la bonne colonne.
    """
    name = file_ext.name.lower()
    if name.endswith(".csv"):
        df_ext = pd.read_csv(
            file_ext,
            dtype=str,
            encoding="utf-8",
            engine="python",
            sep=None  # sniff auto ; si besoin tu pourras forcer ";"
        )
    else:
        df_ext = pd.read_excel(
            file_ext,
            dtype=str,
        )
    # strip espaces
    df_ext = df_ext.apply(lambda col: col.str.strip() if col.dtype == "object" else col)
    return df_ext


def convertir_colonne_date(df_ext, col_date):
    """
    Convertit la colonne 'col_date' en objet date (AAAA-MM-JJ, AAAAMMJJ, etc.).
    Renvoie un df avec une nouvelle colonne 'Date_externe' de type date.
    """
    def to_date(s):
        # essai AAAAMMJJ
        try:
            return pd.to_datetime(s, format="%Y%m%d", errors="raise").date()
        except Exception:
            pass
        # essai AAAA-MM-JJ
        try:
            return pd.to_datetime(s, format="%Y-%m-%d", errors="raise").date()
        except Exception:
            pass
        # fallback auto
        try:
            return pd.to_datetime(s, errors="raise").date()
        except Exception:
            return pd.NaT

    df2 = df_ext.copy()
    df2["Date_externe"] = df2[col_date].apply(to_date)
    return df2


def fusionner_fec_externe(df_daily_full, df_ext_with_date):
    """
    Fusion sur les dates présentes dans le FEC uniquement.
    df_daily_full : colonnes ['EcritureDate','Cumul_TOTAL']
    df_ext_with_date : contient 'Date_externe' en type date
    Sortie : merge left FEC -> externe
    """
    # on crée une colonne date pure côté FEC
    base = df_daily_full.copy()
    base["Date"] = base["EcritureDate"].dt.date

    merged = pd.merge(
        base,
        df_ext_with_date,
        left_on="Date",
        right_on="Date_externe",
        how="left"
    )

    # nettoyage colonnes techniques en double
    merged = merged.drop(columns=["Date_externe"], errors="ignore")

    return merged


# -----------------------
# Streamlit App
# -----------------------

st.title("Analyse FEC au jour le jour + Fusion données externes")

# On initialise les clés session_state pour ne rien perdre entre les étapes
if "df_fec" not in st.session_state:
    st.session_state.df_fec = None

if "df_daily_full" not in st.session_state:
    st.session_state.df_daily_full = None

if "df_ext" not in st.session_state:
    st.session_state.df_ext = None

if "merged" not in st.session_state:
    st.session_state.merged = None

st.header("Étape 1 : Charger le FEC et produire le CA journalier (jours sans écriture = 0)")

fec_files = st.file_uploader(
    "Importe ton (ou tes) FEC .txt/.csv (max 6)",
    type=["txt", "csv"],
    accept_multiple_files=True,
    key="fec_uploader"
)

if fec_files:
    if len(fec_files) > 6:
        st.warning("Max 6 fichiers.")
    else:
        # bouton pour calculer et figer dans la session
        if st.button("📊 Calculer le CA journalier FEC"):
            df_fec = lire_fec(fec_files)
            if df_fec is not None and not df_fec.empty:
                # bornes par défaut :
                start_date_default = df_fec.index.min().date()
                end_date_default   = df_fec.index.max().date()

                # on stocke en session pour la suite
                st.session_state.df_fec = df_fec
                st.session_state.start_date_default = start_date_default
                st.session_state.end_date_default = end_date_default
            else:
                st.error("FEC vide ou illisible.")

# Affichage des contrôles de filtrage si on a bien le FEC en session
if st.session_state.df_fec is not None:
    st.write("FEC chargé ✅")

    # bornes compte
    start_compte = st.number_input("Compte début", min_value=0, value=70000000)
    end_compte   = st.number_input("Compte fin",   min_value=0, value=70999999)

    # bornes dates
    start_date_in = st.date_input(
        "Date début d'analyse",
        value=st.session_state.start_date_default
    )
    end_date_in = st.date_input(
        "Date fin d'analyse",
        value=st.session_state.end_date_default
    )

    if st.button("🧮 Générer le tableau journalier avec trous = 0"):
        df_daily_full = calc_ca_journalier_avec_trous(
            st.session_state.df_fec,
            start_compte,
            end_compte,
            start_date_in,
            end_date_in
        )

        st.session_state.df_daily_full = df_daily_full
        st.session_state.merged = None  # reset fusion si on relance l'étape 1

    # Si déjà calculé en session : affiche
    if st.session_state.df_daily_full is not None:
        st.subheader("CA Journalier (avec jours vides à 0)")
        st.dataframe(st.session_state.df_daily_full)

        # Graph
        img_buf = plot_ca(st.session_state.df_daily_full)
        st.image(img_buf, caption="Cumul TOTAL journalier (FEC)")

        # Export Excel
        excel_buf_daily = io.BytesIO()
        st.session_state.df_daily_full.to_excel(excel_buf_daily, index=False)
        excel_buf_daily.seek(0)

        st.download_button(
            label="💾 Télécharger CA_Journalier_FEC.xlsx",
            data=excel_buf_daily,
            file_name="CA_Journalier_FEC.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )


st.header("Étape 2 : Importer les données externes et fusionner")

ext_file = st.file_uploader(
    "Données externes (.xlsx / .xls / .csv)",
    type=["xlsx", "xls", "csv"],
    accept_multiple_files=False,
    key="ext_uploader"
)

if ext_file:
    # On lit et on stocke en session IMMÉDIATEMENT
    st.session_state.df_ext = lire_externe(ext_file)

if st.session_state.df_ext is not None:
    st.write("Fichier externe chargé ✅")
    st.write("Aperçu :")
    st.dataframe(st.session_state.df_ext.head())

    # Choix de la colonne date dans le fichier externe
    possible_date_cols = list(st.session_state.df_ext.columns)
    col_date_choisie = st.selectbox(
        "Quelle colonne correspond à la date dans les données externes ?",
        possible_date_cols,
        key="ext_date_col_select"
    )

    # Bouton de fusion : on fige le résultat fusionné dans la session
    if st.button("🔗 Fusionner avec le FEC (LEFT JOIN sur les dates FEC uniquement)"):
        if st.session_state.df_daily_full is None:
            st.error("Tu dois d'abord générer le CA journalier (Étape 1).")
        else:
            df_ext_dated = convertir_colonne_date(st.session_state.df_ext, col_date_choisie)

            merged = fusionner_fec_externe(
                st.session_state.df_daily_full,
                df_ext_dated
            )

            st.session_state.merged = merged

# Affichage du merged si dispo
if st.session_state.merged is not None:
    st.subheader("Résultat fusionné FEC + Externe")
    st.dataframe(st.session_state.merged)

    excel_buf_merged = io.BytesIO()
    st.session_state.merged.to_excel(excel_buf_merged, index=False)
    excel_buf_merged.seek(0)

    st.download_button(
        label="💾 Télécharger Fusion_FEC_Externe.xlsx",
        data=excel_buf_merged,
        file_name="Fusion_FEC_Externe.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
