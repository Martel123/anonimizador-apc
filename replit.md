# Anonimizador Legal + Plataforma de Centros de Conciliación

## Overview
This project now includes a standalone **Legal Document Anonymizer** as its main feature (accessible at `/`). The anonymizer automatically detects and replaces personal identifiable information (PII) in legal documents using regex-based patterns, without requiring any paid external services.

The platform also includes a multi-tenant SaaS system for Conciliation Centers, accessible via `/dashboard-app` for authenticated users.

## Legal Anonymizer (Main Feature)
- **URL**: `/` (home page) or `/anonymizer`
- **Supported formats**: DOCX and PDF (text-based)
- **PII Detection**: DNI, RUC, emails, phones, addresses, names, expedientes, casillas, juzgados
- **Placeholders**: `{{DNI_1}}`, `{{PERSONA_1}}`, `{{EMAIL_1}}`, etc.
- **Output**: Anonymized document + detailed report (JSON/TXT)
- **Privacy**: Files processed in memory, auto-deleted after 30 minutes
- **No paid services**: Pure regex/rule-based detection

## Original Platform Overview
This project is a multi-tenant SaaS web platform built with Flask, designed for Conciliation Centers. It enables multiple centers to register, each operating with isolated data, including templates, users, documents, and styles. Each tenant benefits from customizable branding (logo, contact information) and the system supports three distinct user roles, along with a subscription-based plan system (Basic, Medium, Advanced) that gates access to features like user count, document limits, and AI argumentation. The platform aims to streamline document generation, improve legal argumentation with AI, and provide a comprehensive management system for conciliation processes.

## User Preferences
I prefer detailed explanations and an iterative development approach. I expect the agent to ask before making major changes and to provide clear reasoning for its suggestions. I want the agent to prioritize secure, multi-tenant architectural solutions.

## System Architecture

### UI/UX Decisions
The Legal Anonymizer uses the **APC Jurídica** brand identity with a corporate, serious, professional aesthetic:
- **Typography**: Inter font family (system-ui fallback)
- **Color Palette**: 
  - Background: #F2F2F2 (gray), Cards: #FFFFFF (white)
  - Header: #0B0B0B (black), Primary action: #B30000 (APC red)
  - Borders: #E5E5E5, Secondary text: #6F6F6F
- **Style**: Soft rounded corners (12px), subtle shadows, no gradients/glassmorphism
- **Components**: White cards on gray background, black header with APC logo
- **Design file**: See `design_guidelines.md` for complete specifications

The platform (dashboard) uses Tailwind CSS with Roboto font. Each tenant can customize branding (logo, colors).

### Technical Implementations
The core application is built with Flask, leveraging Flask-Login for authentication and Flask-SQLAlchemy for ORM. PostgreSQL (hosted on Neon) is used as the primary database. Document generation is handled by `python-docx`, and AI capabilities are powered by the OpenAI API (specifically `gpt-4o`). Gunicorn serves the application.

### Feature Specifications
- **Multi-Tenancy:** Data isolation is enforced using `tenant_id` on all primary tables and separate document storage folders.
- **Role-Based Access Control:** Three roles: `super_admin` (platform owner), `admin_estudio` (center administrator), and `usuario_estudio` (conciliator/collaborator), with specific decorators for access control.
- **Subscription Management:** Features are gated based on a tenant's subscription plan (Basic, Medium, Advanced), controlling user limits, document generation, and access to AI argumentation.
- **Audit Logging:** Comprehensive logging of significant events per tenant, including user actions and plan changes.
- **Dynamic Document Generation:** Users can select templates, fill dynamic fields, and generate professional `.docx` documents with tenant-specific branding (logo, contact info, styles).
- **AI Argumentation Module:** An asynchronous system using a background worker for enhancing legal documents. It allows specific sections (Facts, Grounds, Petition) to be improved, detects user intent (explanation vs. modification), and formats output with tenant-specific styles.
- **Legal AI Agent (APC IA):** An intelligent agent built with OpenAI function calling, offering a ChatGPT-like interface for legal professionals. It can execute specialized tools for case information retrieval, document management, document generation, strategy drafting, task creation, and cost estimation.

### System Design Choices
- **Database Schema:** A normalized PostgreSQL database schema includes tables for `tenants`, `audit_logs`, `tipos_acta` (document types), `users`, `document_records`, `plantillas` (templates), `estilos` (styles), `campos_plantilla` (template fields), and `estilos_documento` (document styling preferences), all designed with `tenant_id` for isolation.
- **Asynchronous Processing:** The AI Argumentation module utilizes background workers to handle long-running tasks, preventing UI timeouts and ensuring a smooth user experience.
- **Security:** All data access is filtered by `tenant_id` and `user_id` to ensure strict multi-tenant and user-level isolation. Two-factor authentication (2FA) is supported for enhanced user security.

## External Dependencies
- **PostgreSQL:** For database management (hosted on Neon).
- **OpenAI API:** For AI functionalities, including document argumentation and the APC IA agent (using `gpt-4o`).
- **Gunicorn:** As the WSGI HTTP Server.
- **python-docx:** For programmatic creation and modification of Word documents.