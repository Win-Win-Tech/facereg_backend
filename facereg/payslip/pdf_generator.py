"""
Payslip PDF Generation Utility
Uses ReportLab to generate professional PDF payslips
"""

from io import BytesIO
from datetime import datetime

# ReportLab is an optional dependency used for PDF generation.
# Importing it at module load can cause Django to fail startup if the
# package isn't installed. Wrap imports so the module still loads and
# provides a clear error if `generate_payslip_pdf` is invoked without
# reportlab available.
REPORTLAB_AVAILABLE = True
try:
    from reportlab.lib.pagesizes import A4, LETTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch, cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
    from reportlab.pdfgen import canvas
except Exception:
    REPORTLAB_AVAILABLE = False


def generate_payslip_pdf(payslip_data, template_data=None):
    """
    Generate a PDF payslip from payslip data and optional template configuration.
    
    Args:
        payslip_data (dict): Payslip record data with fields like:
            - id, employee_name, employee_email, month, period
            - gross_salary, deductions, net_pay, etc.
        template_data (dict): Template configuration with styling options:
            - company_name, company_address, header_text, footer_text
            - header_color, footer_color, page_size, orientation, font_size
    
    Returns:
        BytesIO: PDF file content as bytes
    """
    
    if not REPORTLAB_AVAILABLE:
        raise ImportError(
            "reportlab is required to generate PDFs. Install it with `pip install reportlab`."
        )

    # Default template if none provided
    if not template_data:
        template_data = {
            'company_name': 'Company Inc.',
            'company_address': '123 Business Street',
            'company_email': 'hr@company.com',
            'company_phone': '+1-800-123-4567',
            'header_text': 'PAYSLIP',
            'footer_text': 'Confidential - For Employee Use Only',
            'header_color': '#1e40af',
            'footer_color': '#64748b',
            'page_size': 'A4',
            'orientation': 'portrait',
            'font_size': 10,
        }
    
    # Determine page size
    page_size = A4 if template_data.get('page_size', 'A4') == 'A4' else LETTER
    
    # Create PDF in memory
    pdf_buffer = BytesIO()
    doc = SimpleDocTemplate(
        pdf_buffer,
        pagesize=page_size,
        rightMargin=0.5*inch,
        leftMargin=0.5*inch,
        topMargin=0.5*inch,
        bottomMargin=0.5*inch,
    )
    
    # Container for PDF elements
    elements = []
    
    # Define styles
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor(template_data.get('header_color', '#1e40af')),
        spaceAfter=12,
        alignment=1,  # Center
        fontName='Helvetica-Bold',
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=11,
        textColor=colors.HexColor(template_data.get('header_color', '#1e40af')),
        spaceAfter=6,
        fontName='Helvetica-Bold',
    )
    
    normal_style = ParagraphStyle(
        'CustomNormal',
        parent=styles['Normal'],
        fontSize=template_data.get('font_size', 10),
        leading=14,
    )
    
    # Header with company info
    company_name = Paragraph(
        f"<b>{template_data.get('company_name', 'Company Inc.')}</b>",
        title_style
    )
    elements.append(company_name)
    
    company_info = template_data.get('company_address', '')
    if template_data.get('company_email'):
        company_info += f" | {template_data.get('company_email')}"
    if template_data.get('company_phone'):
        company_info += f" | {template_data.get('company_phone')}"
    
    if company_info:
        elements.append(Paragraph(company_info, normal_style))
    
    elements.append(Spacer(1, 0.2*inch))
    
    # Payslip header text
    elements.append(Paragraph(
        template_data.get('header_text', 'PAYSLIP'),
        heading_style
    ))
    
    elements.append(Spacer(1, 0.15*inch))
    
    # Employee and period info
    period_text = payslip_data.get('period') or payslip_data.get('month', '')
    employee_info_data = [
        ['Employee Name:', payslip_data.get('employee_name', '—')],
        ['Employee ID:', payslip_data.get('employee_id', '—')],
        ['Email:', payslip_data.get('employee_email', '—')],
        ['Period:', period_text],
    ]
    
    employee_table = Table(employee_info_data, colWidths=[2*inch, 3*inch])
    employee_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#334155')),
        ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
        ('ALIGN', (1, 0), (1, -1), 'LEFT'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(employee_table)
    elements.append(Spacer(1, 0.2*inch))
    
    # Earnings and Deductions table
    earnings_data = [['EARNINGS', '', 'DEDUCTIONS', '']]
    
    # Parse earnings
    earnings_list = []
    if payslip_data.get('basic_salary'):
        earnings_list.append(['Basic Salary', f"₹{payslip_data.get('basic_salary', 0):,.2f}"])
    if payslip_data.get('allowances'):
        earnings_list.append(['Allowances', f"₹{payslip_data.get('allowances', 0):,.2f}"])
    if payslip_data.get('bonus'):
        earnings_list.append(['Bonus', f"₹{payslip_data.get('bonus', 0):,.2f}"])
    
    # Parse deductions
    deductions_list = []
    if payslip_data.get('pf_deduction'):
        deductions_list.append(['Provident Fund', f"₹{payslip_data.get('pf_deduction', 0):,.2f}"])
    if payslip_data.get('income_tax'):
        deductions_list.append(['Income Tax', f"₹{payslip_data.get('income_tax', 0):,.2f}"])
    if payslip_data.get('deductions'):
        deductions_list.append(['Other Deductions', f"₹{payslip_data.get('deductions', 0):,.2f}"])
    
    # Make rows equal length
    max_rows = max(len(earnings_list), len(deductions_list))
    for i in range(max_rows):
        if i < len(earnings_list):
            earnings_data.append([
                earnings_list[i][0],
                earnings_list[i][1],
                deductions_list[i][0] if i < len(deductions_list) else '',
                deductions_list[i][1] if i < len(deductions_list) else '',
            ])
        else:
            earnings_data.append(['', '', deductions_list[i][0], deductions_list[i][1]])
    
    # Add totals row
    gross = payslip_data.get('gross_salary', 0)
    total_deductions = payslip_data.get('total_deductions', 0)
    earnings_data.append([
        'GROSS SALARY',
        f"₹{gross:,.2f}",
        'TOTAL DEDUCTIONS',
        f"₹{total_deductions:,.2f}",
    ])
    
    earnings_table = Table(earnings_data, colWidths=[1.75*inch, 1.25*inch, 1.75*inch, 1.25*inch])
    earnings_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(template_data.get('header_color', '#1e40af'))),
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (2, 0), (2, -1), 'LEFT'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e2e8f0')),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elements.append(earnings_table)
    
    elements.append(Spacer(1, 0.15*inch))
    
    # Net Pay (prominently displayed)
    net_pay = payslip_data.get('net_pay', 0)
    net_pay_style = ParagraphStyle(
        'NetPay',
        parent=styles['Normal'],
        fontSize=14,
        textColor=colors.HexColor('#059669'),
        fontName='Helvetica-Bold',
        alignment=2,  # Right align
    )
    elements.append(Paragraph(f"<b>NET PAY: ₹{net_pay:,.2f}</b>", net_pay_style))
    
    elements.append(Spacer(1, 0.2*inch))
    
    # Footer
    footer_text = template_data.get('footer_text', 'Confidential - For Employee Use Only')
    footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontSize=8,
        textColor=colors.HexColor(template_data.get('footer_color', '#64748b')),
        alignment=1,  # Center
    )
    
    elements.append(Spacer(1, 0.1*inch))
    elements.append(Paragraph(f"<i>{footer_text}</i>", footer_style))
    
    # Generated on
    generated_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    elements.append(Paragraph(
        f"<i>Generated on {generated_date}</i>",
        footer_style
    ))
    
    # Build PDF
    doc.build(elements)
    pdf_buffer.seek(0)
    
    return pdf_buffer
