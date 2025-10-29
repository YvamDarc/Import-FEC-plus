import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import io
from datetime import datetime
import plotly.graph_objects as go
import plotly.express as px

# -------------------------------------------------
#                 UTILITAIRES
# -------------------------------------------------

def lire_fec(uploaded_files):
    """
    Lit 1..6 fichiers FEC txt/csv tabulés, concatène, nettoie, convertit les champs critiques,
    et retourne un DataFrame indexé par EcritureDate (datetime).
    """
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

    # Vérifs colonnes essentielles
    if "EcritureDate" not in df.columns:
        st.error("Colonne 'EcritureDate' absente du FEC.")
        return None

    if "CompteNum" not in df.columns:
        st.error("Colonne 'CompteNum' absente du FEC.")
        return None

    # Conversion des dates comptables
    df["EcritureDate"] = pd.to_datetime(
        df["EcritureDate"],
        format="%Y%m%d",
        errors="coerce"
    )

    # Conversion éventuelle PieceDate (optionnel)
    if "PieceDate" in df.columns:
        df["PieceDate"] = pd.to_datetime(
            df["PieceDate"],
            format="%Y%m%d",
            errors="coerce"
        )

    # Conversion des montants (virgule FR -> point)
    for col_montant in ["Debit", "Credit", "Montantdevise"]:
        if col_montant in df.columns:
            df[col_montant] = (
                df[col_montant]
                .str.replace(",", ".", regex=False)
                .str.replace(" ", "", regex=False)
            )
            df[col_montant] = pd.to_numeric(df[col_montant], errors="coerce")

    # CompteNum : tronquer puis convertir
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
    - génère toutes les dates de la plage
    - remplit les jours sans écriture avec 0
    Retourne un df_journalier avec colonnes [EcritureDate, Cumul_TOTAL]
    """

    work = df_fec.copy()

    # filtre comptes
    work = work[
        (work["CompteNum"] >= start_compte) &
        (work["CompteNum"] <= end_compte)
    ]

    # sécurité montants
    for col_montant in ["Debit", "Credit"]:
        if col_montant in work.columns:
            work[col_montant] = (
                work[col_montant]
                .astype(str)
                .str.replace(",", ".", regex=False)
                .str.replace(" ", "", regex=False)
            )
            work[col_montant] = pd.to_numeric(work[col_montant], errors="coerce")
        else:
            work[col_montant] = 0.0

    # TOTAL = Crédit - Débit
    work["TOTAL"] = work["Credit"] - work["Debit"]

    # filtre période date
    work_period = work.loc[start_date:end_date]

    # agrégat journalier
    df_daily = (
        work_period
        .groupby(work_period.index)["TOTAL"]
        .sum()
        .reset_index()
    )
    df_daily.columns = ["EcritureDate", "Cumul_TOTAL"]

    # calendrier continu complet
    all_days = pd.date_range(start=start_date, end=end_date, freq="D")
    df_all = pd.DataFrame({"EcritureDate": all_days})

    # merge pour insérer les trous
    df_daily_full = pd.merge(df_all, df_daily, on="EcritureDate", how="left")

    # remplir 0 les jours sans écriture
    df_daily_full["Cumul_TOTAL"] = df_daily_full["Cumul_TOTAL"].fillna(0)

    return df_daily_full


def plot_ca_matplotlib(df_daily_full):
    """
    Trace le CA quotidien (Cumul_TOTAL) avec matplotlib.
    Retourne un buffer PNG.
    """
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
    plt.close(fig)
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
            sep=None  # sniff automatique ; ajustable
        )
    else:
        # Excel
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
        if pd.isna(s):
            return pd.NaT
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
    base = df_daily_full.copy()
    base["Date"] = base["EcritureDate"].dt.date

    merged = pd.merge(
        base,
        df_ext_with_date,
        left_on="Date",
        right_on="Date_externe",
        how="left"
    )

    merged = merged.drop(columns=["Date_externe"], errors="ignore")

    return merged


def build_plotly_timeseries(df_time, date_col, cols_to_plot):
    """
    Construit une figure Plotly avec plusieurs colonnes numériques tracées dans le temps.
    On ne fait pas de multi-axes Y pour garder quelque chose de lisible/robuste.
    """
    fig = go.Figure()
    for col in cols_to_plot:
        if col == date_col:
            continue
        if pd.api.types.is_numeric_dtype(df_time[col]):
            fig.add_trace(go.Scatter(
                x=df_time[date_col],
                y=df_time[col],
                mode="lines+markers",
                name=col
            ))

    fig.update_layout(
        title="Évolution dans le temps",
        xaxis_title="Date",
        yaxis_title="Valeurs",
        legend_title="Variables"
    )

    return fig


def build_plotly_corr_heatmap(df_num):
    """
    Calcule la matrice de corrélation et renvoie une figure Plotly heatmap.
    """
    corr = df_num.corr(method="pearson")

    fig = px.imshow(
        corr,
        text_auto=True,
        aspect="auto",
        color_continuous_scale="RdBu_r",
        zmin=-1,
        zmax=1,
        title="Matrice de corrélations (Pearson)"
    )

    return fig, corr


# -------------------------------------------------
#          INITIALISATION SESSION
# -------------------------------------------------

if "df_fec" not in st.session_state:
    st.session_state.df_fec = None

if "start_date_default" not in st.session_state:
    st.session_state.start_date_default = None

if "end_date_default" not in st.session_state:
    st.session_state.end_date_default = None

if "df_daily_full" not in st.session_state:
    st.session_state.df_daily_full = None

if "df_ext" not in st.session_state:
    st.session_state.df_ext = None

if "merged" not in st.session_state:
    st.session_state.merged = None


# -------------------------------------------------
#                INTERFACE
# -------------------------------------------------

st.title("Analyse FEC journalière ✚ Données externes")

# ----------------- ÉTAPE 1 : FEC -----------------

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
        # Bouton pour charger et initialiser les bornes
        if st.button("📊 Calculer le CA journalier FEC"):
            df_fec = lire_fec(fec_files)
            if df_fec is not None and not df_fec.empty:
                st.session_state.df_fec = df_fec
                st.session_state.start_date_default = df_fec.index.min().date()
                st.session_state.end_date_default = df_fec.index.max().date()
            else:
                st.error("FEC vide ou illisible.")

# Si on a le FEC en mémoire
if st.session_state.df_fec is not None:
    st.success("FEC chargé ✅")

    # bornes comptes
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

    if st.button("🧮 Générer le tableau journalier avec jours vides = 0"):
        df_daily_full = calc_ca_journalier_avec_trous(
            st.session_state.df_fec,
            start_compte,
            end_compte,
            start_date_in,
            end_date_in
        )
        st.session_state.df_daily_full = df_daily_full
        st.session_state.merged = None  # reset fusion si on relance l'étape 1

    # Affichage si déjà calculé
    if st.session_state.df_daily_full is not None:
        st.subheader("CA Journalier (avec jours vides à 0)")
        st.dataframe(st.session_state.df_daily_full)

        # Graphique CA seul (matplotlib simple)
        img_buf = plot_ca_matplotlib(st.session_state.df_daily_full)
        st.image(img_buf, caption="Cumul TOTAL journalier (FEC)")

        # Export Excel du CA journalier seul
        excel_buf_daily = io.BytesIO()
        st.session_state.df_daily_full.to_excel(excel_buf_daily, index=False)
        excel_buf_daily.seek(0)

        st.download_button(
            label="💾 Télécharger CA_Journalier_FEC.xlsx",
            data=excel_buf_daily,
            file_name="CA_Journalier_FEC.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )


# ------------- ÉTAPE 2 : EXTERNE + FUSION -------------

st.header("Étape 2 : Importer les données externes et fusionner")

ext_file = st.file_uploader(
    "Données externes (.xlsx / .xls / .csv)",
    type=["xlsx", "xls", "csv"],
    accept_multiple_files=False,
    key="ext_uploader"
)

if ext_file:
    st.session_state.df_ext = lire_externe(ext_file)

if st.session_state.df_ext is not None:
    st.success("Fichier externe chargé ✅")
    st.write("Aperçu du fichier externe :")
    st.dataframe(st.session_state.df_ext.head())

    # Choisir la colonne date externe
    possible_date_cols = list(st.session_state.df_ext.columns)
    col_date_choisie = st.selectbox(
        "Quelle colonne correspond à la date dans les données externes ?",
        possible_date_cols,
        key="ext_date_col_select"
    )

    # Fusion
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

# ----------------- ÉTAPE 3 : ANALYSE / VIZ -----------------

if st.session_state.merged is not None:
    st.header("Étape 3 : Analyse, sélection de colonnes, téléchargement, graphiques")

    st.subheader("Résultat fusionné FEC + Externe (brut)")
    st.dataframe(st.session_state.merged.head())

    st.markdown("### Colonnes à conserver dans le dataset final")
    all_cols = list(st.session_state.merged.columns)

    cols_keep = st.multiselect(
        "Colonnes du dataset final :",
        options=all_cols,
        default=all_cols,
        key="cols_keep_select"
    )

    if len(cols_keep) == 0:
        st.warning("Sélectionne au moins une colonne.")
    else:
        # Dataset filtré par l'utilisateur
        df_final = st.session_state.merged[cols_keep].copy()

        # Assurer une colonne de temps pour la suite
        if "EcritureDate" in st.session_state.merged.columns and "EcritureDate" not in df_final.columns:
            df_final["EcritureDate"] = st.session_state.merged["EcritureDate"]

        if "Date" in st.session_state.merged.columns and "Date" not in df_final.columns:
            df_final["Date"] = st.session_state.merged["Date"]

        st.subheader("Dataset final filtré")
        st.dataframe(df_final)

        # Fichier téléchargeable = df_final
        excel_buf_filtered = io.BytesIO()
        df_final.to_excel(excel_buf_filtered, index=False)
        excel_buf_filtered.seek(0)

        st.download_button(
            label="💾 Télécharger le dataset filtré (Excel)",
            data=excel_buf_filtered,
            file_name="Dataset_filtre.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        # ---------------- GRAPH TEMPOREL ----------------
        st.markdown("### Graphique multi-indicateurs (Plotly)")

        # choix de la colonne date pour l'axe X
        if "EcritureDate" in df_final.columns:
            default_date_col = "EcritureDate"
        elif "Date" in df_final.columns:
            default_date_col = "Date"
        else:
            default_date_col = None

        if default_date_col is not None:
            date_col_for_plot = st.selectbox(
                "Colonne date pour l'axe X :",
                options=[c for c in df_final.columns if c in ["EcritureDate", "Date"]],
                index=0 if "EcritureDate" in [c for c in df_final.columns if c in ["EcritureDate", "Date"]] else 0,
                key="date_for_plot_select"
            )

            # On force le type datetime pour l'axe X
            df_plot = df_final.copy()
            df_plot[date_col_for_plot] = pd.to_datetime(df_plot[date_col_for_plot], errors="coerce")

            # choix des colonnes numériques à tracer
            numeric_cols_all = [
                c for c in df_plot.columns
                if c != date_col_for_plot and pd.api.types.is_numeric_dtype(df_plot[c])
            ]

            cols_for_chart = st.multiselect(
                "Quelles colonnes numériques afficher sur le graphique temporel ?",
                options=numeric_cols_all,
                default=numeric_cols_all,
                key="cols_for_chart_select"
            )

            if len(cols_for_chart) == 0:
                st.info("Sélectionne au moins une colonne numérique pour tracer.")
            else:
                fig_timeseries = build_plotly_timeseries(
                    df_plot,
                    date_col_for_plot,
                    [date_col_for_plot] + cols_for_chart  # on lui fournit la date + les séries
                )
                st.plotly_chart(fig_timeseries, use_container_width=True)
        else:
            st.info("Aucune colonne de date détectée pour tracer l'évolution temporelle.")

        # ---------------- HEATMAP CORRÉLATIONS ----------------
        st.markdown("### Heatmap de corrélations (Plotly)")

        # On ne prend que les colonnes numériques du df_final filtré
        df_num = df_final.select_dtypes(include=[float, int])

        if df_num.shape[1] < 2:
            st.info("Pas assez de colonnes numériques pour calculer une matrice de corrélations.")
        else:
            # on laisse l'utilisateur choisir quelles colonnes numériques inclure dans la heatmap
            cols_for_corr = st.multiselect(
                "Colonnes numériques à inclure dans la matrice de corrélations :",
                options=list(df_num.columns),
                default=list(df_num.columns),
                key="cols_for_corr_select"
            )

            if len(cols_for_corr) < 2:
                st.info("Choisis au moins deux colonnes numériques pour la corrélation.")
            else:
                df_num_sub = df_num[cols_for_corr].copy()
                fig_corr, corr_matrix = build_plotly_corr_heatmap(df_num_sub)

                st.plotly_chart(fig_corr, use_container_width=True)

                st.write("Tableau des corrélations :")
                st.dataframe(corr_matrix.round(3))
