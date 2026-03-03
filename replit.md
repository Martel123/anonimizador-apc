# Anonimizador Legal + Plataforma de Centros de Conciliación

## Overview
This project features a Legal Document Anonymizer and a multi-tenant SaaS platform for Conciliation Centers. The anonymizer detects and replaces Personally Identifiable Information (PII) in legal documents (DOCX and PDF) using regex patterns and optional AI enhancement, ensuring privacy by processing files in memory and auto-deleting them. The platform also offers a robust credit system for monetization, based on document page usage.

The multi-tenant SaaS component, accessible via `/dashboard-app`, provides tools for Conciliation Centers, including dynamic document generation, AI-powered legal argumentation, and comprehensive management features. It supports customizable branding, role-based access control, and subscription-based feature gating. The overall vision is to streamline legal document processing and enhance legal argumentation through advanced AI capabilities, targeting legal professionals and conciliation centers.

## User Preferences
I prefer detailed explanations and an iterative development approach. I expect the agent to ask before making major changes and to provide clear reasoning for its suggestions. I want the agent to prioritize secure, multi-tenant architectural solutions.

## System Architecture

### UI/UX Decisions
The Legal Anonymizer uses the **APC Jurídica** brand identity, featuring a corporate, professional aesthetic with the Inter font family, a color palette centered on `#F2F2F2` (background), `#FFFFFF` (cards), `#0B0B0B` (header), and `#B30000` (primary action). It employs soft rounded corners (12px) and subtle shadows. The dashboard utilizes Tailwind CSS with Roboto font, allowing tenant-specific branding customization.

### Technical Implementations
The application is built with Flask, utilizing Flask-Login for authentication and Flask-SQLAlchemy for ORM. PostgreSQL (Neon) serves as the primary database. Document generation is handled by `python-docx`. AI capabilities are powered by the OpenAI API (specifically `gpt-4o`), and Gunicorn serves the application.

### Feature Specifications
- **Legal Anonymizer**: Detects and redacts 21+ categories of PII, outputting anonymized documents and reports. Features a triple-layer zero-leak guarantee with an 8-stage detection pipeline and optional local/OpenAI NER enhancement. Monetized via a page-based credit system.
- **Multi-Tenancy**: Enforced data isolation per tenant using `tenant_id` across all primary tables and separate document storage.
- **Role-Based Access Control**: `super_admin`, `admin_estudio`, and `usuario_estudio` roles with specific access decorators.
- **Subscription Management**: Features gated by tenant subscription plans (Basic, Medium, Advanced).
- **Audit Logging**: Comprehensive logging of significant events per tenant.
- **Dynamic Document Generation**: Allows users to select templates, fill dynamic fields, and generate `.docx` documents with tenant-specific branding.
- **AI Argumentation Module**: Asynchronous background worker system for enhancing legal document sections (Facts, Grounds, Petition) with AI, detecting user intent and formatting output.
- **Legal AI Agent (APC IA)**: An intelligent agent using OpenAI function calling for legal professionals, offering tools for case information retrieval, document management, generation, strategy drafting, task creation, and cost estimation.
- **Auth Security Hardening**: Includes robust password policies (min. 10 chars, complexity requirements), login rate limiting (anti-brute-force), login event logging, and unusual IP detection.

### System Design Choices
- **Database Schema**: Normalized PostgreSQL schema with `tenant_id` for isolation across tables like `tenants`, `users`, `document_records`, `plantillas`, etc.
- **Asynchronous Processing**: Background workers handle long-running tasks for AI argumentation.
- **Security**: Strict data access filtering by `tenant_id` and `user_id` ensures multi-tenant and user-level isolation. Two-factor authentication (2FA) is supported.

## External Dependencies
- **PostgreSQL**: Hosted on Neon for database management.
- **OpenAI API**: Used for AI functionalities, including document argumentation and the APC IA agent (`gpt-4o`).
- **Gunicorn**: Serves as the WSGI HTTP Server.
- **python-docx**: Used for programmatic creation and modification of Word documents.