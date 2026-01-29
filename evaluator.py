"""
Evaluator - Métricas y evaluación para el anonimizador legal
==============================================================
Genera reportes de calidad: recall, precision, posibles fugas, sobreanonimización.
"""

import re
import json
import logging
from typing import Dict, List, Tuple, Set, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

DNI_PATTERN = re.compile(r'\b[0-9]{8}\b')
RUC_PATTERN = re.compile(r'\b(?:10|15|17|20)[0-9]{9}\b')
EMAIL_PATTERN = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', re.IGNORECASE)
PHONE_PATTERN = re.compile(r'(?:\+51\s*)?9[0-9]{8}\b')
ACCOUNT_PATTERN = re.compile(r'\b[0-9]{10,20}\b')


@dataclass
class EvaluationReport:
    """Reporte de evaluación del anonimizado."""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    total_entities_detected: int = 0
    entities_by_type: Dict[str, int] = field(default_factory=dict)
    entities_replaced: int = 0
    
    potential_leaks: List[Dict[str, Any]] = field(default_factory=list)
    potential_overanon: List[Dict[str, Any]] = field(default_factory=list)
    
    privacy_recall: float = 0.0
    precision: float = 0.0
    final_score: float = 0.0
    
    filter_stats: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def detect_potential_leaks(original_text: str, anonymized_text: str) -> List[Dict[str, Any]]:
    """
    Detecta posibles fugas de PII en el texto anonimizado.
    Busca patrones que deberían haber sido reemplazados pero quedaron.
    """
    leaks = []
    
    token_pattern = re.compile(r'\{\{[A-Z_]+_\d+\}\}')
    
    for match in DNI_PATTERN.finditer(anonymized_text):
        value = match.group()
        context_start = max(0, match.start() - 30)
        context_end = min(len(anonymized_text), match.end() + 30)
        context = anonymized_text[context_start:context_end]
        
        if not token_pattern.search(context):
            money_context = re.search(r'(?:S/\.?|US?\$|\%|soles|dólares)', context, re.IGNORECASE)
            if not money_context:
                leaks.append({
                    'type': 'DNI',
                    'value': value,
                    'position': match.start(),
                    'context': context,
                    'severity': 'high'
                })
    
    for match in EMAIL_PATTERN.finditer(anonymized_text):
        if not token_pattern.search(match.group()):
            leaks.append({
                'type': 'EMAIL',
                'value': match.group(),
                'position': match.start(),
                'context': anonymized_text[max(0, match.start()-20):min(len(anonymized_text), match.end()+20)],
                'severity': 'high'
            })
    
    for match in PHONE_PATTERN.finditer(anonymized_text):
        if not token_pattern.search(match.group()):
            leaks.append({
                'type': 'TELEFONO',
                'value': match.group(),
                'position': match.start(),
                'context': anonymized_text[max(0, match.start()-20):min(len(anonymized_text), match.end()+20)],
                'severity': 'medium'
            })
    
    return leaks


def detect_potential_overanon(entities_applied: List[Tuple[str, str, int, int, float]], 
                               filter_results: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    """
    Detecta posibles casos de sobreanonimización.
    """
    from legal_filters import LEGAL_WHITELIST_EXACT, is_legal_title, contains_legal_verb
    
    overanon = []
    
    for entity in entities_applied:
        entity_type, value, start, end, confidence = entity
        
        if entity_type == 'PERSONA':
            value_upper = value.upper().strip()
            
            if value_upper in LEGAL_WHITELIST_EXACT:
                overanon.append({
                    'type': entity_type,
                    'value': value,
                    'reason': 'whitelist_violation',
                    'severity': 'high'
                })
                continue
            
            if is_legal_title(value):
                overanon.append({
                    'type': entity_type,
                    'value': value,
                    'reason': 'legal_title_tokenized',
                    'severity': 'high'
                })
                continue
            
            if contains_legal_verb(value):
                overanon.append({
                    'type': entity_type,
                    'value': value,
                    'reason': 'contains_legal_verb',
                    'severity': 'medium'
                })
                continue
            
            words = value.split()
            if len(words) > 5:
                overanon.append({
                    'type': entity_type,
                    'value': value[:50] + '...' if len(value) > 50 else value,
                    'reason': 'too_many_words',
                    'severity': 'low'
                })
    
    return overanon


def calculate_metrics(original_text: str, 
                      anonymized_text: str,
                      entities_detected: List[Tuple[str, str, int, int, float]],
                      entities_applied: List[Tuple[str, str, int, int, float]],
                      filter_results: Optional[List[Any]] = None) -> EvaluationReport:
    """
    Calcula métricas de calidad del anonimizado.
    """
    report = EvaluationReport()
    
    report.total_entities_detected = len(entities_detected)
    report.entities_replaced = len(entities_applied)
    
    for entity in entities_applied:
        entity_type = entity[0]
        report.entities_by_type[entity_type] = report.entities_by_type.get(entity_type, 0) + 1
    
    report.potential_leaks = detect_potential_leaks(original_text, anonymized_text)
    
    report.potential_overanon = detect_potential_overanon(entities_applied, filter_results)
    
    original_pii_count = 0
    original_pii_count += len(list(DNI_PATTERN.finditer(original_text)))
    original_pii_count += len(list(EMAIL_PATTERN.finditer(original_text)))
    original_pii_count += len(list(PHONE_PATTERN.finditer(original_text)))
    
    anon_pii_remaining = len(report.potential_leaks)
    
    if original_pii_count > 0:
        report.privacy_recall = max(0, (original_pii_count - anon_pii_remaining) / original_pii_count)
    else:
        report.privacy_recall = 1.0
    
    if report.entities_replaced > 0:
        overanon_penalty = len(report.potential_overanon) / report.entities_replaced
        report.precision = max(0, 1.0 - overanon_penalty)
    else:
        report.precision = 1.0
    
    report.final_score = 0.7 * report.privacy_recall + 0.3 * report.precision
    
    if filter_results:
        accepted = sum(1 for r in filter_results if r.accepted)
        rejected = sum(1 for r in filter_results if not r.accepted)
        report.filter_stats = {
            'total': len(filter_results),
            'accepted': accepted,
            'rejected': rejected,
            'rejection_rate': rejected / len(filter_results) if filter_results else 0
        }
    
    if report.privacy_recall < 0.95:
        report.warnings.append(f"Privacy recall bajo ({report.privacy_recall:.1%}): revisar entidades no detectadas")
    
    if report.precision < 0.90:
        report.warnings.append(f"Precision baja ({report.precision:.1%}): posible sobreanonimización")
    
    if len(report.potential_leaks) > 0:
        report.warnings.append(f"{len(report.potential_leaks)} posibles fugas de PII detectadas")
    
    return report


def report_to_dict(report: EvaluationReport) -> Dict[str, Any]:
    """Convierte el reporte a diccionario para JSON."""
    return {
        'timestamp': report.timestamp,
        'entities': {
            'detected': report.total_entities_detected,
            'replaced': report.entities_replaced,
            'by_type': report.entities_by_type
        },
        'quality': {
            'privacy_recall': round(report.privacy_recall, 4),
            'precision': round(report.precision, 4),
            'final_score': round(report.final_score, 4)
        },
        'issues': {
            'potential_leaks': report.potential_leaks[:10],
            'potential_overanon': report.potential_overanon[:10],
            'leak_count': len(report.potential_leaks),
            'overanon_count': len(report.potential_overanon)
        },
        'filter_stats': report.filter_stats,
        'warnings': report.warnings
    }


def log_evaluation(report: EvaluationReport):
    """Registra el reporte en los logs del servidor."""
    report_dict = report_to_dict(report)
    
    logging.info(f"=== EVALUATION REPORT ===")
    logging.info(f"Entities: {report.total_entities_detected} detected, {report.entities_replaced} replaced")
    logging.info(f"By type: {report.entities_by_type}")
    logging.info(f"Privacy Recall: {report.privacy_recall:.1%}")
    logging.info(f"Precision: {report.precision:.1%}")
    logging.info(f"Final Score: {report.final_score:.1%}")
    
    if report.potential_leaks:
        logging.warning(f"Potential leaks ({len(report.potential_leaks)}): {report.potential_leaks[:3]}")
    
    if report.potential_overanon:
        logging.warning(f"Potential over-anonymization ({len(report.potential_overanon)}): {report.potential_overanon[:3]}")
    
    for warning in report.warnings:
        logging.warning(f"Evaluation warning: {warning}")
    
    logging.info(f"========================")
    
    return report_dict
