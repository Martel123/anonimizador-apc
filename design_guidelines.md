# Design Guidelines: Legal Document Generation Platform

## Design Approach

**Selected Approach:** Design System - Material Design  
**Justification:** This is a professional legal productivity tool requiring clarity, trust, and efficiency. Material Design provides the structured, clean aesthetic appropriate for law firms while ensuring excellent usability for form-heavy workflows.

## Core Design Principles

1. **Professional Trust:** Clean, organized layouts that convey competence and reliability
2. **Form-First Clarity:** Optimize for data entry with clear labels, generous spacing, and logical grouping
3. **Efficiency Focus:** Minimize friction in the document generation workflow
4. **Status Transparency:** Clear feedback at every step of the process

## Typography

**Font Family:** Roboto (via Google Fonts CDN)
- Headings: Roboto Medium (500), sizes: text-3xl (main title), text-2xl (section headers), text-xl (card titles)
- Body: Roboto Regular (400), text-base for forms and content
- Labels: Roboto Medium (500), text-sm for form labels
- Metadata: Roboto Regular (400), text-sm for timestamps and secondary info

## Layout System

**Spacing Primitives:** Tailwind units of 2, 4, 6, 8, 12, and 16
- Component padding: p-6 or p-8
- Form field spacing: gap-4 or gap-6
- Section margins: mb-8 or mb-12
- Container padding: px-4 md:px-8

**Container Strategy:**
- Main content: max-w-4xl mx-auto for forms
- History table: max-w-6xl mx-auto
- Card-based layouts with consistent rounded corners (rounded-lg)

## Component Library

### Navigation
- Clean header with logo/title and minimal navigation
- Fixed top bar with shadow for depth (shadow-sm)
- Navigation items right-aligned

### Forms (Primary Focus)
- **Document Type Selector:** Large, prominent dropdown with clear label, h-12 with proper padding
- **Input Fields:** 
  - Full-width text inputs with h-12 height
  - Clear labels above inputs (text-sm font-medium mb-2)
  - Placeholder text for guidance
  - Border styling with focus states
- **Textarea Fields:** For argumentos and conclusion, min-h-32, auto-resize capability
- **Field Grouping:** Related fields grouped in cards with p-6 padding
- **Required Field Indicators:** Asterisk (*) for required fields

### Primary CTA
- Large "Generar Documento" button, full-width on mobile, inline on desktop
- Height: h-12, generous padding px-8
- Prominent placement at form bottom
- Loading state with spinner during generation

### History Table
- Clean table with alternating row treatment
- Columns: Fecha, Tipo de Documento, Demandante, Acciones
- Compact download buttons/links in actions column
- Responsive: Stack to cards on mobile
- Header row with subtle background differentiation

### Cards
- Elevation with shadow-md
- Rounded corners: rounded-lg
- Consistent padding: p-6 or p-8
- White/neutral background

### Feedback Elements
- Success message cards (p-4, rounded-md) after document generation
- Error messages inline near relevant fields
- Loading states with centered spinner

## Page-Specific Layouts

### Index (Main Form)
- Centered single-column layout (max-w-4xl)
- Hero section: Simple header with title "Generador de Documentos Jurídicos" (text-3xl) and brief description (text-lg)
- Form card with clear sections:
  1. Document type selector (prominent, mb-8)
  2. Personal information group (invitado, demandante1, dni)
  3. Arguments section (argumento1-3 as textareas)
  4. Conclusion textarea
  5. Generate button (sticky bottom on mobile)

### Historial (History)
- Page title: "Historial de Documentos" (text-3xl, mb-8)
- Responsive table/card grid
- Empty state message if no documents
- "Volver al inicio" link at top

## Images

**No hero images required** - This is a functional tool where clarity and efficiency take precedence over visual marketing.

## Accessibility & Interactions

- All form inputs include proper labels and ARIA attributes
- Keyboard navigation fully supported
- Focus indicators clearly visible
- Button states: Default, hover (subtle elevation increase), active (slight scale), disabled (reduced opacity)
- Form validation messages appear inline below fields
- No distracting animations - only subtle transitions (transition-all duration-200)

## Professional Polish

- Consistent 8px grid alignment throughout
- Generous whitespace prevents cramped feeling
- Clear visual hierarchy through size and weight, not color
- Form fields feel substantial and easy to target (minimum 48px touch targets)
- Professional, trustworthy aesthetic appropriate for legal context