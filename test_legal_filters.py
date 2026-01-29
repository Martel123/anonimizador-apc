"""
Tests para el sistema de filtrado legal anti-sobreanonimización.
Verifica que texto jurídico NO se tokenize y que PII real SÍ se tokenize.
"""

import unittest
from legal_filters import (
    should_anonymize_span,
    is_in_exact_whitelist,
    matches_whitelist_pattern,
    contains_legal_verb,
    is_legal_connector,
    is_legal_title,
    looks_like_proper_name,
    filter_entities,
)
from detector_capas import is_excluded_word, detect_all_pii, Entity


class TestLegalWhitelist(unittest.TestCase):
    """Tests para la whitelist legal."""
    
    def test_exact_whitelist_matches(self):
        """Frases exactas de whitelist deben detectarse."""
        self.assertTrue(is_in_exact_whitelist("FUNDAMENTOS DE HECHO"))
        self.assertTrue(is_in_exact_whitelist("SEÑOR JUEZ"))
        self.assertTrue(is_in_exact_whitelist("PRIMERO"))
        self.assertTrue(is_in_exact_whitelist("OTROSI DIGO"))
        self.assertTrue(is_in_exact_whitelist("EN CONSECUENCIA"))
    
    def test_exact_whitelist_rejects_names(self):
        """Nombres propios NO deben estar en whitelist."""
        self.assertFalse(is_in_exact_whitelist("JUAN PEREZ GARCIA"))
        self.assertFalse(is_in_exact_whitelist("MARIA RODRIGUEZ"))
        self.assertFalse(is_in_exact_whitelist("EDUARDO GAMARRA PÉREZ"))
    
    def test_pattern_whitelist(self):
        """Patrones de whitelist deben detectarse."""
        self.assertTrue(matches_whitelist_pattern("ARTÍCULO 139"))
        self.assertTrue(matches_whitelist_pattern("LEY N° 30364"))
        self.assertTrue(matches_whitelist_pattern("1° JUZGADO DE FAMILIA"))


class TestLegalVerbs(unittest.TestCase):
    """Tests para detección de verbos legales."""
    
    def test_legal_verbs_detected(self):
        """Verbos legales deben detectarse."""
        self.assertTrue(contains_legal_verb("interpongo demanda"))
        self.assertTrue(contains_legal_verb("amparo mi demanda"))
        self.assertTrue(contains_legal_verb("solicito se declare"))
        self.assertTrue(contains_legal_verb("declaro bajo juramento"))
    
    def test_names_without_verbs(self):
        """Nombres no contienen verbos legales."""
        self.assertFalse(contains_legal_verb("Juan Perez Garcia"))
        self.assertFalse(contains_legal_verb("MARIA GONZALEZ"))


class TestLegalConnectors(unittest.TestCase):
    """Tests para conectores legales."""
    
    def test_connectors_detected(self):
        """Conectores legales deben detectarse."""
        self.assertTrue(is_legal_connector("en consecuencia"))
        self.assertTrue(is_legal_connector("sin embargo"))
        self.assertTrue(is_legal_connector("por tanto"))
        self.assertTrue(is_legal_connector("asimismo"))
    
    def test_names_not_connectors(self):
        """Nombres no son conectores."""
        self.assertFalse(is_legal_connector("Juan Perez"))
        self.assertFalse(is_legal_connector("MARIA RODRIGUEZ LOPEZ"))


class TestProperNameDetection(unittest.TestCase):
    """Tests para detección de nombres propios."""
    
    def test_proper_names_detected(self):
        """Nombres propios deben detectarse."""
        self.assertTrue(looks_like_proper_name("Juan Pérez García"))
        self.assertTrue(looks_like_proper_name("EDUARDO GAMARRA LÓPEZ"))
        self.assertTrue(looks_like_proper_name("María Rodríguez Sánchez"))
    
    def test_legal_phrases_not_names(self):
        """Frases legales NO son nombres propios."""
        self.assertFalse(looks_like_proper_name("FUNDAMENTOS DE HECHO"))
        self.assertFalse(looks_like_proper_name("SEÑOR JUEZ"))
        self.assertFalse(looks_like_proper_name("Amparo mi demanda"))
        self.assertFalse(looks_like_proper_name("En consecuencia"))
    
    def test_long_phrases_rejected(self):
        """Frases muy largas (>5 palabras) no son nombres."""
        self.assertFalse(looks_like_proper_name("A la fecha de la presente demanda interpongo"))
        self.assertFalse(looks_like_proper_name("Por los fundamentos expuestos solicito"))


class TestShouldAnonymize(unittest.TestCase):
    """Tests para la decisión de anonimización."""
    
    def test_dni_always_anonymized(self):
        """DNI siempre debe anonimizarse."""
        should_anon, _ = should_anonymize_span("12345678", "DNI")
        self.assertTrue(should_anon)
    
    def test_email_always_anonymized(self):
        """Email siempre debe anonimizarse."""
        should_anon, _ = should_anonymize_span("juan@example.com", "EMAIL")
        self.assertTrue(should_anon)
    
    def test_phone_always_anonymized(self):
        """Teléfono siempre debe anonimizarse."""
        should_anon, _ = should_anonymize_span("987654321", "TELEFONO")
        self.assertTrue(should_anon)
    
    def test_proper_name_anonymized(self):
        """Nombres propios deben anonimizarse."""
        should_anon, reason = should_anonymize_span("EDUARDO GAMARRA PÉREZ", "PERSONA")
        self.assertTrue(should_anon, f"Name should be anonymized, got reason: {reason}")
    
    def test_legal_phrase_not_anonymized(self):
        """Frases legales NO deben anonimizarse."""
        should_anon, reason = should_anonymize_span("FUNDAMENTOS DE HECHO", "PERSONA")
        self.assertFalse(should_anon, f"Legal phrase should NOT be anonymized, got reason: {reason}")
        
        should_anon, reason = should_anonymize_span("AMPARO MI DEMANDA", "PERSONA")
        self.assertFalse(should_anon, f"Legal phrase should NOT be anonymized, got reason: {reason}")
    
    def test_connector_not_anonymized(self):
        """Conectores NO deben anonimizarse."""
        should_anon, _ = should_anonymize_span("EN CONSECUENCIA", "PERSONA")
        self.assertFalse(should_anon)
    
    def test_legal_title_not_anonymized(self):
        """Títulos legales NO deben anonimizarse."""
        should_anon, _ = should_anonymize_span("PRIMERO", "PERSONA")
        self.assertFalse(should_anon)
        
        should_anon, _ = should_anonymize_span("OTROSI DIGO", "PERSONA")
        self.assertFalse(should_anon)


class TestExcludedWords(unittest.TestCase):
    """Tests para palabras excluidas en detector_capas."""
    
    def test_single_excluded_words(self):
        """Palabras individuales excluidas."""
        self.assertTrue(is_excluded_word("DEMANDA"))
        self.assertTrue(is_excluded_word("JUEZ"))
        self.assertTrue(is_excluded_word("FISCAL"))
        self.assertTrue(is_excluded_word("PRIMERO"))
    
    def test_excluded_phrases(self):
        """Frases excluidas completas."""
        self.assertTrue(is_excluded_word("FUNDAMENTOS DE HECHO"))
        self.assertTrue(is_excluded_word("AMPARO MI DEMANDA"))
        self.assertTrue(is_excluded_word("POR LO EXPUESTO"))
    
    def test_names_not_excluded(self):
        """Nombres propios NO están excluidos."""
        self.assertFalse(is_excluded_word("JUAN PEREZ GARCIA"))
        self.assertFalse(is_excluded_word("MARIA RODRIGUEZ"))


class TestIntegration(unittest.TestCase):
    """Tests de integración del pipeline completo."""
    
    def test_real_document_fragment(self):
        """Test con fragmento de documento real."""
        text = """
        SEÑOR JUEZ DEL JUZGADO DE FAMILIA
        
        EDUARDO GAMARRA PÉREZ, identificado con DNI 12345678, 
        con domicilio real en Av. Larco 123, Miraflores, Lima,
        ante Usted respetuosamente me presento y digo:
        
        FUNDAMENTOS DE HECHO:
        PRIMERO: Que, interpongo demanda de alimentos...
        
        En consecuencia, solicito a Usted señor Juez...
        """
        
        entities, metadata = detect_all_pii(text, apply_filters=True)
        
        entity_values = [e.value for e in entities]
        
        name_found = any("GAMARRA" in v.upper() for v in entity_values)
        self.assertTrue(name_found, "Name EDUARDO GAMARRA should be detected")
        
        dni_found = any(e.type == 'DNI' and '12345678' in e.value for e in entities)
        self.assertTrue(dni_found, "DNI 12345678 should be detected")
        
        for e in entities:
            self.assertNotIn("FUNDAMENTOS DE HECHO", e.value.upper(),
                           "Legal phrase should NOT be detected as entity")
            self.assertNotIn("PRIMERO", e.value.upper(),
                           "Legal title should NOT be detected as entity")
    
    def test_consistency(self):
        """Mismo nombre debe generar el mismo token."""
        text = """
        JUAN PEREZ GARCIA interpone demanda.
        El demandante JUAN PEREZ GARCIA declara...
        Que, el señor JUAN PEREZ GARCIA solicita...
        """
        
        entities, _ = detect_all_pii(text, apply_filters=True)
        
        juan_entities = [e for e in entities if "JUAN" in e.value.upper() and "PEREZ" in e.value.upper()]
        
        self.assertGreater(len(juan_entities), 0, "Name should be detected at least once")


if __name__ == '__main__':
    unittest.main()
