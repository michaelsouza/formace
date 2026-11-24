from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTPUT_DIR = DATA_DIR / "normalizados"

SCHOOL_LIST_PATH = DATA_DIR / "ia_na_escola_escolas_participantes.xlsx"
SOURCE_DATA_PATH = DATA_DIR / "ia_na_escola_indicadores_educacionais_2015_2025.xlsx"
OUTPUT_WORKBOOK_PATH = OUTPUT_DIR / "ia_na_escola_normalizado.xlsx"
OUTPUT_SCHOOLS_CSV_PATH = OUTPUT_DIR / "escolas_normalizadas.csv"
OUTPUT_SUMMARY_PATH = OUTPUT_DIR / "resumo_normalizacao.md"

SCHOOL_NAME_COLUMN = "nm_escola"
SCHOOL_ID_COLUMN = "id_escola_inep"

# Divergencias verificadas entre a lista enviada e a base de resultados.
NAME_ALIASES = {
    "CENTRO EDUCACIONAL MOEMA TAVORA": "EEMTI MOEMA TAVORA",
    "EEM HERMINIO BARROSO": "EEMTI HERMINIO BARROSO",
    "EEEP ICARO DE SOUSA": "EEEP ICARO DE SOUSA MOREIRA",
    "3 COLEGIO DA PM DO CEARA TEN MARIO LIMA": "3 COLEGIO DA POLICIA MILITAR DO CEARA TENENTE MARIO LIMA",
    "EEM PROF FRANCISCA LINHARES DE SOUSA": "EEM PROFESSORA FRANCISCA LINHARES DE SOUSA",
    "EEMTI PROF IRACI PEREIRA DE ALCANTARA": "EEMTI PROFESSORA IRACI PEREIRA DE ALCANTARA",
    "LICEU PROF FRANCISCO OSCAR RODRIGUES": "LICEU PROFESSOR FRANCISCO OSCAR RODRIGUES",
}


def normalize_text(value: Any) -> str:
    """Return an uppercase, accent-free key suitable for joins."""
    if pd.isna(value):
        return ""

    text = str(value).replace("\xa0", " ").strip().upper()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"\bPROFA\b\.?", "PROFESSORA", text)
    text = re.sub(r"\bPROFO\b\.?", "PROFESSOR", text)
    text = re.sub(r"\bPROFª\b\.?", "PROFESSORA", text)
    text = re.sub(r"\bPROFº\b\.?", "PROFESSOR", text)
    text = re.sub(r"\bTEN\b\.?", "TEN", text)
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def analysis_block(crede_sefor: Any) -> str:
    normalized = normalize_text(crede_sefor)
    if normalized.startswith("SEFOR"):
        return "SEFOR 01, 02 e 03"
    return "Todas juntas"


def load_source_sheet(sheet_name: str) -> pd.DataFrame:
    raw = pd.read_excel(SOURCE_DATA_PATH, sheet_name=sheet_name, header=None)
    header_index = find_header_index(raw)
    columns = raw.iloc[header_index].tolist()

    data = raw.iloc[header_index + 1 :].copy()
    data.columns = columns
    data = data.dropna(how="all")
    data = data.loc[:, [column for column in data.columns if not pd.isna(column)]]
    data = data.replace({"-": pd.NA, "--": pd.NA})

    return data.reset_index(drop=True)


def find_header_index(raw: pd.DataFrame) -> int:
    candidates: list[int] = []
    for index, row in raw.iterrows():
        values = {normalize_text(value) for value in row.tolist()}
        if "NM ESCOLA" in values and ("CD CREDE SEFOR" in values or "CODIGO DA CREDE SEFOR" in values):
            candidates.append(index)

    if not candidates:
        raise ValueError("Nao foi possivel localizar a linha de cabecalho tecnico.")

    return candidates[-1]


def build_canonical_school_table(sheet_names: list[str]) -> pd.DataFrame:
    frames = []
    for sheet_name in sheet_names:
        sheet = load_source_sheet(sheet_name)
        expected = {
            "cd_crede_sefor",
            "nm_crede_sefor",
            "id_municipio",
            "nm_municipio",
            SCHOOL_ID_COLUMN,
            SCHOOL_NAME_COLUMN,
            "nm_categoria",
        }
        available = [column for column in expected if column in sheet.columns]
        frame = sheet[available].copy()
        frame["aba_origem"] = sheet_name
        frames.append(frame)

    schools = pd.concat(frames, ignore_index=True)
    schools = schools.dropna(subset=[SCHOOL_NAME_COLUMN])
    schools["escola_chave"] = schools[SCHOOL_NAME_COLUMN].map(normalize_text)

    sort_columns = ["escola_chave", "aba_origem"]
    schools = schools.sort_values(sort_columns)
    schools = schools.drop_duplicates(subset=["escola_chave"], keep="first")

    preferred_order = [
        "escola_chave",
        SCHOOL_ID_COLUMN,
        SCHOOL_NAME_COLUMN,
        "cd_crede_sefor",
        "nm_crede_sefor",
        "id_municipio",
        "nm_municipio",
        "nm_categoria",
    ]
    return schools[[column for column in preferred_order if column in schools.columns]].reset_index(drop=True)


def build_school_mapping(canonical_schools: pd.DataFrame) -> pd.DataFrame:
    school_list = pd.read_excel(SCHOOL_LIST_PATH)
    school_list = school_list.rename(
        columns={
            "CREDE/SEFOR": "crede_sefor_lista",
            "ESCOLA": "escola_lista",
        }
    )
    school_list["escola_chave_lista"] = school_list["escola_lista"].map(normalize_text)
    school_list["bloco_analise"] = school_list["crede_sefor_lista"].map(analysis_block)

    canonical_by_key = canonical_schools.set_index("escola_chave").to_dict("index")
    rows: list[dict[str, Any]] = []

    for _, row in school_list.iterrows():
        list_key = row["escola_chave_lista"]
        canonical_key = list_key
        match_type = "exato"

        if canonical_key not in canonical_by_key:
            canonical_key = NAME_ALIASES.get(list_key, "")
            match_type = "alias"

        if canonical_key not in canonical_by_key:
            raise ValueError(f"Escola sem correspondencia na base: {row['escola_lista']!r}")

        canonical = canonical_by_key[canonical_key]
        rows.append(
            {
                "crede_sefor_lista": row["crede_sefor_lista"],
                "escola_lista": clean_display_text(row["escola_lista"]),
                "bloco_analise": row["bloco_analise"],
                "tipo_correspondencia": match_type,
                "escola_chave_lista": list_key,
                "escola_chave_base": canonical_key,
                "id_escola_inep": canonical.get(SCHOOL_ID_COLUMN),
                "escola_base": canonical.get(SCHOOL_NAME_COLUMN),
                "cd_crede_sefor_base": canonical.get("cd_crede_sefor"),
                "crede_sefor_base": canonical.get("nm_crede_sefor"),
                "id_municipio": canonical.get("id_municipio"),
                "municipio": canonical.get("nm_municipio"),
                "categoria": canonical.get("nm_categoria"),
            }
        )

    return pd.DataFrame(rows)


def clean_display_text(value: Any) -> Any:
    if pd.isna(value):
        return value
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def normalize_sheet(sheet_name: str, school_mapping: pd.DataFrame) -> pd.DataFrame:
    sheet = load_source_sheet(sheet_name)
    sheet["escola_chave_base"] = sheet[SCHOOL_NAME_COLUMN].map(normalize_text)

    enrichment_columns = [
        "id_escola_inep",
        "escola_base",
        "escola_lista",
        "crede_sefor_lista",
        "bloco_analise",
        "tipo_correspondencia",
        "escola_chave_base",
    ]
    enrichment = school_mapping[enrichment_columns].copy()
    enrichment = enrichment.drop_duplicates(subset=["escola_chave_base"])

    normalized = sheet.merge(
        enrichment,
        how="inner",
        on="escola_chave_base",
        suffixes=("", "_normalizado"),
    )

    front_columns = [
        "bloco_analise",
        "crede_sefor_lista",
        "escola_lista",
        "escola_base",
        "id_escola_inep",
        "tipo_correspondencia",
    ]
    remaining_columns = [column for column in normalized.columns if column not in front_columns]
    normalized = normalized[front_columns + remaining_columns]
    return normalized


def write_outputs(school_mapping: pd.DataFrame, normalized_sheets: dict[str, pd.DataFrame]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    school_mapping.to_csv(OUTPUT_SCHOOLS_CSV_PATH, index=False, encoding="utf-8")

    with pd.ExcelWriter(OUTPUT_WORKBOOK_PATH, engine="openpyxl") as writer:
        school_mapping.to_excel(writer, sheet_name="ESCOLAS", index=False)
        for sheet_name, sheet in normalized_sheets.items():
            sheet.to_excel(writer, sheet_name=sheet_name, index=False)

    OUTPUT_SUMMARY_PATH.write_text(build_summary(school_mapping, normalized_sheets), encoding="utf-8")


def build_summary(school_mapping: pd.DataFrame, normalized_sheets: dict[str, pd.DataFrame]) -> str:
    exact_count = int((school_mapping["tipo_correspondencia"] == "exato").sum())
    alias_count = int((school_mapping["tipo_correspondencia"] == "alias").sum())
    sefor_count = int((school_mapping["bloco_analise"] == "SEFOR 01, 02 e 03").sum())
    all_count = len(school_mapping)

    lines = [
        "# Resumo da normalização",
        "",
        f"- Escolas normalizadas: {all_count}",
        f"- Bloco SEFOR 01, 02 e 03: {sefor_count} escolas",
        f"- Bloco todas juntas: {all_count} escolas",
        f"- Correspondências exatas: {exact_count}",
        f"- Correspondências por alias: {alias_count}",
        "",
        "## Arquivos gerados",
        "",
        f"- `{OUTPUT_WORKBOOK_PATH.relative_to(ROOT)}`",
        f"- `{OUTPUT_SCHOOLS_CSV_PATH.relative_to(ROOT)}`",
        "",
        "## Abas normalizadas",
        "",
    ]

    for sheet_name, sheet in normalized_sheets.items():
        school_count = sheet["id_escola_inep"].nunique()
        lines.append(f"- `{sheet_name}`: {len(sheet)} linhas, {school_count} escolas")

    aliases = school_mapping[school_mapping["tipo_correspondencia"] == "alias"]
    if not aliases.empty:
        lines.extend(["", "## Aliases aplicados", ""])
        for _, row in aliases.iterrows():
            lines.append(f"- `{row['escola_lista']}` -> `{row['escola_base']}`")

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    sheet_names = pd.ExcelFile(SOURCE_DATA_PATH).sheet_names
    canonical_schools = build_canonical_school_table(sheet_names)
    school_mapping = build_school_mapping(canonical_schools)
    normalized_sheets = {
        sheet_name: normalize_sheet(sheet_name, school_mapping)
        for sheet_name in sheet_names
    }
    write_outputs(school_mapping, normalized_sheets)

    print(f"Escolas normalizadas: {len(school_mapping)}")
    print(f"Arquivo Excel: {OUTPUT_WORKBOOK_PATH.relative_to(ROOT)}")
    print(f"Mapa CSV: {OUTPUT_SCHOOLS_CSV_PATH.relative_to(ROOT)}")
    print(f"Resumo: {OUTPUT_SUMMARY_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
