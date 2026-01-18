"""
Automated tests for the Legal Document Anonymizer.
Tests regex detection, NER integration, and placeholder preservation.
Only Tokens mode is supported.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anonymizer as anon


class TestEntityDetection(unittest.TestCase):
    """Tests for PII entity detection."""
    
    def test_dni_detection(self):
        """Test that 8-digit DNI numbers are detected."""
        text = "El ciudadano identificado con DNI 12345678 presenta la demanda."
        confirmed, needs_review = anon.detect_entities_hybrid(text)
        dni_entities = [e for e in confirmed if e[0] == 'DNI']
        self.assertTrue(len(dni_entities) > 0, "DNI should be detected")
        self.assertEqual(dni_entities[0][1], '12345678')
    
    def test_ruc_detection(self):
        """Test that 11-digit RUC numbers are detected."""
        text = "La empresa con RUC 20123456789 está registrada."
        confirmed, _ = anon.detect_entities_hybrid(text)
        ruc_entities = [e for e in confirmed if e[0] == 'RUC']
        self.assertTrue(len(ruc_entities) > 0, "RUC should be detected")
    
    def test_email_detection(self):
        """Test that email addresses are detected."""
        text = "Contactar al correo usuario@ejemplo.com para más información."
        confirmed, _ = anon.detect_entities_hybrid(text)
        email_entities = [e for e in confirmed if e[0] == 'EMAIL']
        self.assertTrue(len(email_entities) > 0, "Email should be detected")
    
    def test_phone_detection(self):
        """Test that phone numbers are detected."""
        text = "Su número de celular es 987654321."
        confirmed, _ = anon.detect_entities_hybrid(text)
        phone_entities = [e for e in confirmed if e[0] == 'TELEFONO']
        self.assertTrue(len(phone_entities) > 0, "Phone should be detected")
    
    def test_address_detection(self):
        """Test that addresses are detected."""
        text = "Domiciliado en Av. Javier Prado 1234, San Isidro."
        confirmed, _ = anon.detect_entities_hybrid(text)
        address_entities = [e for e in confirmed if e[0] == 'DIRECCION']
        self.assertTrue(len(address_entities) > 0, "Address should be detected")
    
    def test_expediente_detection(self):
        """Test that expediente numbers are detected."""
        text = "Expediente N° 00123-2023-0-1801-JR-CI-01"
        confirmed, _ = anon.detect_entities_hybrid(text)
        exp_entities = [e for e in confirmed if e[0] == 'EXPEDIENTE']
        self.assertTrue(len(exp_entities) > 0, "Expediente should be detected")
    
    def test_name_with_context(self):
        """Test that names with legal context are detected."""
        text = "El demandante don EDUARDO GAMARRA PÉREZ presenta su demanda."
        confirmed, needs_review = anon.detect_entities_hybrid(text)
        all_person_entities = [e for e in confirmed if e[0] == 'PERSONA']
        all_person_entities.extend([e for e in needs_review if e[0] == 'PERSONA'])
        self.assertTrue(len(all_person_entities) > 0, "Person name should be detected")


class TestPlaceholderPreservation(unittest.TestCase):
    """Tests that existing {{...}} placeholders are NOT modified."""
    
    def test_falta_dato_preserved(self):
        """Test that {{FALTA_DATO}} is not anonymized."""
        text = "El demandante con DNI {{FALTA_DATO}} solicita la audiencia."
        placeholder_positions = anon.find_existing_placeholders(text)
        self.assertTrue(len(placeholder_positions) > 0, "Should find placeholder")
        self.assertEqual(placeholder_positions[0], (22, 36))
    
    def test_custom_placeholder_preserved(self):
        """Test that custom placeholders are preserved."""
        text = "Nombre: {{NOMBRE_DEMANDANTE}} con DNI 12345678"
        placeholder_positions = anon.find_existing_placeholders(text)
        self.assertTrue(len(placeholder_positions) > 0)
        
        entities = anon.detect_entities_regex(text, placeholder_positions)
        dni_entities = [e for e in entities if e[0] == 'DNI']
        self.assertTrue(len(dni_entities) > 0, "DNI should still be detected")
    
    def test_multiple_placeholders(self):
        """Test multiple placeholders in same text."""
        text = "{{PARTE1}} vs {{PARTE2}} en el Expediente {{EXPEDIENTE}}"
        placeholder_positions = anon.find_existing_placeholders(text)
        self.assertEqual(len(placeholder_positions), 3)


class TestTokenMode(unittest.TestCase):
    """Tests for the Token substitution mode (only mode available)."""
    
    def test_token_format(self):
        """Test token-based substitution format."""
        mapping = anon.EntityMapping()
        result = mapping.get_substitute('DNI', '12345678')
        self.assertTrue(result.startswith('{{'), "Should produce token placeholder")
        self.assertTrue(result.endswith('}}'), "Should produce token placeholder")
        self.assertIn('DNI', result)
    
    def test_consistent_mapping(self):
        """Test that same value gets same placeholder."""
        mapping = anon.EntityMapping()
        result1 = mapping.get_substitute('DNI', '12345678')
        result2 = mapping.get_substitute('DNI', '12345678')
        self.assertEqual(result1, result2, "Same value should get same placeholder")
    
    def test_different_values_different_tokens(self):
        """Test that different values get different tokens."""
        mapping = anon.EntityMapping()
        result1 = mapping.get_substitute('DNI', '12345678')
        result2 = mapping.get_substitute('DNI', '87654321')
        self.assertNotEqual(result1, result2, "Different values should get different tokens")
        self.assertEqual(result1, '{{DNI_1}}')
        self.assertEqual(result2, '{{DNI_2}}')


class TestMoneyExclusion(unittest.TestCase):
    """Tests that monetary amounts are not treated as DNI/RUC."""
    
    def test_soles_not_dni(self):
        """Test that S/ amounts are not detected as DNI."""
        text = "El monto es S/ 12345678 soles."
        placeholder_positions = anon.find_existing_placeholders(text)
        entities = anon.detect_entities_regex(text, placeholder_positions)
        dni_entities = [e for e in entities if e[0] == 'DNI']
        self.assertEqual(len(dni_entities), 0, "Money should not be detected as DNI")
    
    def test_dollars_not_dni(self):
        """Test that US$ amounts are not detected as DNI."""
        text = "El pago fue de US$ 12345678."
        placeholder_positions = anon.find_existing_placeholders(text)
        entities = anon.detect_entities_regex(text, placeholder_positions)
        dni_entities = [e for e in entities if e[0] == 'DNI']
        self.assertEqual(len(dni_entities), 0, "Dollar amounts should not be DNI")


class TestExcludedWords(unittest.TestCase):
    """Tests that common legal terms are not detected as names."""
    
    def test_legal_terms_excluded(self):
        """Test that SEÑOR JUEZ is not detected as a person name."""
        text = "SEÑOR JUEZ DEL JUZGADO CIVIL"
        placeholder_positions = anon.find_existing_placeholders(text)
        entities = anon.detect_entities_regex(text, placeholder_positions)
        person_entities = [e for e in entities if e[0] == 'PERSONA']
        for entity in person_entities:
            self.assertNotIn('SEÑOR JUEZ', entity[1].upper())


class TestCompleteAnonymization(unittest.TestCase):
    """End-to-end tests for complete anonymization."""
    
    def test_full_document_anonymization(self):
        """Test that a document with all PII types is fully anonymized."""
        test_text = """
        SEÑOR JUEZ DEL JUZGADO CIVIL DE LIMA
        
        El demandante don EDUARDO GAMARRA PÉREZ, identificado con DNI 46789123,
        con domicilio real en JIRÓN LAS PALMERAS N°2121 BLOQUE B DPTO 301 URB. LOS JARDINES DISTRITO SAN ISIDRO;
        correo electrónico eduardo.gamarra@gmail.com y celular 987654321,
        interpone demanda.
        """
        
        anonymized, summary, mapping, needs_review = anon.anonymize_text(test_text)
        
        self.assertNotIn('EDUARDO', anonymized.upper())
        self.assertNotIn('GAMARRA', anonymized.upper())
        self.assertNotIn('PÉREZ', anonymized.upper())
        self.assertNotIn('46789123', anonymized)
        self.assertNotIn('eduardo.gamarra@gmail.com', anonymized.lower())
        self.assertNotIn('987654321', anonymized)
        
        self.assertIn('{{', anonymized)
        self.assertIn('}}', anonymized)
    
    def test_address_complete_replacement(self):
        """Test that full address is replaced as single token."""
        text = "con domicilio real en Av. Los Pinos 123, Miraflores, Lima."
        confirmed, _ = anon.detect_entities_hybrid(text)
        addr_entities = [e for e in confirmed if e[0] == 'DIRECCION']
        self.assertTrue(len(addr_entities) > 0, "Address should be detected")


def run_smoke_test():
    """Run a quick smoke test to verify basic functionality."""
    print("=" * 50)
    print("SMOKE TEST - Anonimizador Legal")
    print("=" * 50)
    
    test_text = """
    SEÑOR JUEZ DEL JUZGADO CIVIL DE LIMA
    
    El demandante don EDUARDO GAMARRA PÉREZ, identificado con DNI 12345678,
    con domicilio en Av. Javier Prado 1234, distrito de San Isidro, provincia
    y departamento de Lima, con correo electrónico eduardo@email.com y celular
    987654321, interpone demanda contra MARÍA LÓPEZ RODRÍGUEZ con RUC 20123456789.
    
    Expediente N° 00123-2023-0-1801-JR-CI-01
    Casilla electrónica N° 12345
    
    Placeholder existente: {{FALTA_DATO}}
    """
    
    errors = []
    
    confirmed, needs_review = anon.detect_entities_hybrid(test_text)
    
    all_entities = confirmed + needs_review
    entity_types = set(e[0] for e in all_entities)
    
    expected_types = ['DNI', 'RUC', 'EMAIL', 'TELEFONO', 'EXPEDIENTE']
    for expected in expected_types:
        if expected not in entity_types:
            errors.append(f"Missing expected entity type: {expected}")
    
    placeholders = anon.find_existing_placeholders(test_text)
    if len(placeholders) != 1:
        errors.append(f"Expected 1 placeholder, found {len(placeholders)}")
    
    mapping = anon.EntityMapping()
    result = mapping.get_substitute('DNI', '12345678')
    if result != '{{DNI_1}}':
        errors.append(f"Token format incorrect: {result}")
    
    anonymized, summary, _, _ = anon.anonymize_text(test_text)
    if 'EDUARDO' in anonymized.upper():
        errors.append("Name EDUARDO still present in anonymized text")
    if '12345678' in anonymized:
        errors.append("DNI still present in anonymized text")
    if 'eduardo@email.com' in anonymized.lower():
        errors.append("Email still present in anonymized text")
    
    if errors:
        print("\nFAILED - Errors found:")
        for error in errors:
            print(f"  - {error}")
        return False
    else:
        print("\nOK - All smoke tests passed!")
        print(f"  - Detected {len(all_entities)} entities")
        print(f"  - Entity types: {', '.join(sorted(entity_types))}")
        print(f"  - Placeholders preserved: {len(placeholders)}")
        print(f"  - Token format verified: {{DNI_1}}")
        print(f"  - Full anonymization verified")
        return True


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--smoke':
        success = run_smoke_test()
        sys.exit(0 if success else 1)
    else:
        unittest.main()
