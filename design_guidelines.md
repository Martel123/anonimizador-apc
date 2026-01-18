# APC Jurídica - Design Guidelines

## Brand Identity
- **Brand**: APC Jurídica - Legal technology company from Peru
- **Style**: Corporate, serious, sober, professional, premium
- **Target audience**: Lawyers, law firms, legal institutions
- **Feel**: Institutional legal software - reliable, stable, trustworthy

## Color Palette

### Primary Colors
- **Background (general)**: `#F2F2F2` (light gray)
- **Cards/Panels**: `#FFFFFF` (white)
- **Header/Top bar**: `#0B0B0B` (black)
- **Secondary black**: `#141414`

### Brand Colors
- **Primary action (APC red)**: `#B30000`
- **Soft red**: `#D63A3A`

### Neutral Colors
- **Secondary text gray**: `#6F6F6F`
- **Border/divider gray**: `#E5E5E5`
- **White**: `#FFFFFF`

### Semantic Colors
- **Success**: `#166534` (sober green)
- **Error**: `#B30000` (APC red)
- **Warning**: `#B45309` (sober amber)
- **Info**: `#1E40AF` (sober blue)

## Typography

### Font Family
```css
font-family: 'Inter', system-ui, -apple-system, sans-serif;
```

### Hierarchy
- **H1 (Page titles)**: 24px, semibold/bold, `#0B0B0B`
- **H2 (Section titles)**: 20px, semibold, `#0B0B0B`
- **H3 (Card titles)**: 16px, medium, `#141414`
- **Body text**: 14px, regular, `#141414`
- **Secondary text**: 14px, regular, `#6F6F6F`
- **Small/caption**: 12px, regular, `#6F6F6F`

## Component Styles

### Cards
```css
background: #FFFFFF;
border-radius: 12px;
border: 1px solid #E5E5E5;
box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
padding: 24px;
```

### Primary Button (CTA)
```css
background: #B30000;
color: #FFFFFF;
border-radius: 12px;
padding: 16px 24px;
font-weight: 500;
font-size: 16px;
```
- Hover: `#8B0000` (darker)
- Icons: Shield or lock if available

### Secondary Button
```css
background: #F2F2F2;
color: #141414;
border: 1px solid #E5E5E5;
border-radius: 12px;
padding: 12px 20px;
font-weight: 500;
```

### Inputs/Selectors/Toggles
```css
background: #FFFFFF;
border: 1px solid #E5E5E5;
border-radius: 8px;
padding: 12px 16px;
```
- Focus: border `#B30000` or `#141414`
- No browser defaults

### Toggle Switches
- Off: `#E5E5E5` background
- On: `#B30000` background
- Knob: `#FFFFFF`

## Layout

### Header
```css
background: #0B0B0B;
color: #FFFFFF;
```
- Contains APC logo
- Height: 64px

### Page Structure
- Background: `#F2F2F2`
- Content in white cards
- Max-width: 1024px centered
- Padding: 32px horizontal, 24px vertical

### Spacing Scale
- xs: 4px
- sm: 8px
- md: 16px
- lg: 24px
- xl: 32px
- 2xl: 48px

## Visual Rules

### DO
- Use subtle shadows (very light)
- Use soft rounded corners (12-16px)
- Keep clean, minimal interface
- Use clear visual hierarchy
- Maintain professional appearance

### DO NOT
- Gradients
- Glassmorphism
- Neon effects
- Bright/flashy colors
- Futuristic or crypto styles
- Heavy shadows

## States

### Success Messages
```css
background: #F0FDF4;
border: 1px solid #86EFAC;
color: #166534;
```

### Error Messages
```css
background: #FEF2F2;
border: 1px solid #FECACA;
color: #B30000;
```

### Warning Messages
```css
background: #FFFBEB;
border: 1px solid #FDE68A;
color: #B45309;
```

### Info Messages
```css
background: #EFF6FF;
border: 1px solid #BFDBFE;
color: #1E40AF;
```

## Entity Type Colors (Anonymizer)
- DNI: Red (`#B30000` bg light)
- RUC: Orange (`#EA580C` bg light)
- EMAIL: Blue (`#2563EB` bg light)
- TELEFONO: Green (`#16A34A` bg light)
- PERSONA: Purple (`#7C3AED` bg light)
- DIRECCION: Amber (`#D97706` bg light)
- EXPEDIENTE: Pink (`#DB2777` bg light)
- CASILLA: Cyan (`#0891B2` bg light)
- JUZGADO: Indigo (`#4F46E5` bg light)
