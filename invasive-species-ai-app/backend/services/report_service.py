import json
import logging

logger = logging.getLogger(__name__)

# Mapear os valores internos para os valores aceites pelo CHECK constraint da tabela
_SOURCE_TYPE_MAP = {
    "Llama3.2_agent":    "langchain_agent",
    "template_fallback": "template_fallback",
}

try:
    from supabase_client import supabase as _sb
    _USE_SUPABASE = True
except Exception as _err:
    _sb = None
    _USE_SUPABASE = False
    logger.warning("Supabase não configurado — relatórios não serão guardados. (%s)", _err)


def save_report(
    level: str,
    report_text: str,
    source: str,
    validation: dict,
    species: str = None,
    municipality: str = None,
) -> None:
    if not _USE_SUPABASE:
        return
    try:
        source_type = _SOURCE_TYPE_MAP.get(source, "template_fallback")
        _sb.table("report").insert({
            "source_type":       source_type,
            "content":           report_text,
            "validation_result": json.dumps(validation, ensure_ascii=False),
        }).execute()
        logger.info("Relatório '%s' guardado (source_type=%s).", level, source_type)
    except Exception as exc:
        logger.warning("Não foi possível guardar relatório: %s", exc)
