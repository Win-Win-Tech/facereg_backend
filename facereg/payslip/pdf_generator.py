"""
Payslip PDF Generation Utility
Uses ReportLab to generate professional PDF payslips
"""

from io import BytesIO
from datetime import datetime
from decimal import Decimal
import os

from django.core.files.base import ContentFile
from django.conf import settings

# ReportLab is an optional dependency used for PDF generation.
REPORTLAB_AVAILABLE = True
try:
    from reportlab.lib.pagesizes import A4, LETTER, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch, cm, mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, Image
    from reportlab.pdfgen import canvas
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT, TA_JUSTIFY
except Exception:
    REPORTLAB_AVAILABLE = False


def save_payslip_pdf(payslip_record):
    """
    Generate and save PDF for a payslip record.
    """
    if not REPORTLAB_AVAILABLE:
        return
    
    # Prepare data
    payslip_data = {
        'employee_name': payslip_record.employee.name,
        'employee_code': payslip_record.employee.employee_code,
        'employee_email': payslip_record.employee.email,
        'designation': payslip_record.employee.designation,
        'department': payslip_record.employee.department,
        'joining_date': payslip_record.employee.joining_date,
        'month': payslip_record.month,
        'gross_salary': payslip_record.gross_salary,
        'total_earnings': payslip_record.total_earnings,
        'total_deductions': payslip_record.total_deductions,
        'net_pay': payslip_record.net_pay,
        'field_values': payslip_record.field_values,
        'attendance': {
            'present_days': payslip_record.present_days,
            'absent_days': payslip_record.absent_days,
            'paid_leave_days': payslip_record.paid_leave_days,
            'unpaid_leave_days': payslip_record.unpaid_leave_days,
            'holiday_count': payslip_record.holiday_count,
            'weekoff_count': payslip_record.weekoff_count,
            'working_days': payslip_record.working_days,
        },
        # Extra fields
        'uan_no': getattr(payslip_record.employee, 'uan_no', '-'),
        'pf_no': getattr(payslip_record.employee, 'pf_no', '-'),
        'esi_no': getattr(payslip_record.employee, 'esi_no', '-'),
        'bank_name': getattr(payslip_record.employee, 'bank_name', '-'),
        'account_no': getattr(payslip_record.employee, 'account_no', '-'),
        'pan_no': getattr(payslip_record.employee, 'pan_no', '-'),
    }
    
    # Get template data
    template = payslip_record.template
    template_data = {
        'company_name': template.company_name,
        'company_address': template.company_address,
        'company_email': template.company_email,
        'company_phone': template.company_phone,
        'company_gstin': template.company_gstin,
        'company_logo': template.company_logo,
        'header_text': template.header_text,
        'footer_text': template.footer_text,
        'page_size': template.page_size,
        'orientation': template.orientation,
        'font_size': template.font_size,
    }
    
    # Generate PDF
    pdf_buffer = generate_payslip_pdf(payslip_data, template_data, payslip_record.field_config)
    
    # Save
    filename = f"payslip_{payslip_record.employee.employee_code}_{payslip_record.month}.pdf"
    payslip_record.pdf_file.save(filename, ContentFile(pdf_buffer.getvalue()), save=True)


def generate_payslip_pdf(payslip_data, template_data=None, field_config=None):
    """
    Generate a Strict Grid Layout PDF payslip (Hitasoft Style).
    """
    
    if not REPORTLAB_AVAILABLE:
        raise ImportError("reportlab is required to generate PDFs.")

    if not template_data:
        template_data = {}
    
    # --- Configuration ---
    page_size_name = template_data.get('page_size', 'A4')
    orientation = template_data.get('orientation', 'portrait')
    
    if page_size_name == 'A4':
        page_size = A4
    else:
        page_size = LETTER
        
    if orientation == 'landscape':
        page_size = landscape(page_size)
        
    # Reduce base font size for a denser look
    base_font_size = 8 
    
    pdf_buffer = BytesIO()
    doc = SimpleDocTemplate(
        pdf_buffer,
        pagesize=page_size,
        rightMargin=0.5*inch,
        leftMargin=0.5*inch,
        topMargin=0.5*inch,
        bottomMargin=0.5*inch,
    )
    
    elements = []
    styles = getSampleStyleSheet()
    
    # --- Styles ---
    # Compact styles
    style_center = ParagraphStyle('Center', parent=styles['Normal'], alignment=TA_CENTER, fontSize=base_font_size, leading=10)
    style_bold_center = ParagraphStyle('BoldCenter', parent=style_center, fontName='Helvetica-Bold')
    style_left = ParagraphStyle('Left', parent=styles['Normal'], alignment=TA_LEFT, fontSize=base_font_size, leading=10)
    style_bold_left = ParagraphStyle('BoldLeft', parent=style_left, fontName='Helvetica-Bold')
    style_right = ParagraphStyle('Right', parent=styles['Normal'], alignment=TA_RIGHT, fontSize=base_font_size, leading=10)
    style_bold_right = ParagraphStyle('BoldRight', parent=style_right, fontName='Helvetica-Bold')
    
    # --- Header Section (Logo + Company Info) ---
    # Hitasoft Style: Logo Left, Company Name (Bold, Large) Right/Center, Address below.
    # Actually, Hitasoft image has Logo Left, and Company Details in a box on the right?
    # No, it looks like a single row with Logo in Col 1, and Company Details in Col 2.
    
    logo_img = None
    if template_data.get('company_logo'):
        try:
            logo_field = template_data['company_logo']
            if hasattr(logo_field, 'path') and os.path.exists(logo_field.path):
                logo_img = Image(logo_field.path)
                img_height = 0.6 * inch # Smaller logo
                aspect = logo_img.imageWidth / float(logo_img.imageHeight)
                logo_img.drawWidth = img_height * aspect
                logo_img.drawHeight = img_height
        except Exception:
            pass

    co_name = template_data.get('company_name', 'Company Name')
    # Company Name larger and bold
    co_info = [Paragraph(f"<b><font size=12>{co_name}</font></b>", style_center)]
    
    addr = template_data.get('company_address', '')
    if addr:
        co_info.append(Paragraph(addr, style_center))
        
    contact = []
    if template_data.get('company_phone'): contact.append(f"Phone: {template_data['company_phone']}")
    if template_data.get('company_email'): contact.append(f"Email: {template_data['company_email']}")
    if contact:
        co_info.append(Paragraph(" | ".join(contact), style_center))
        
    # Header Table
    if orientation == 'landscape':
        avail_width = 10.5 * inch
    else:
        avail_width = 7.2 * inch # Slightly narrower to fit margins
        
    if logo_img:
        # Logo exists: 2 Columns (Logo Left, Info Center/Right)
        header_data = [[logo_img, co_info]]
        header_table = Table(header_data, colWidths=[1.5*inch, avail_width - 1.5*inch])
        header_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (0, 0), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (1, 0), (1, 0), 'CENTER'),
            ('BOX', (0, 0), (-1, -1), 1, colors.black),
            ('LEFTPADDING', (0, 0), (-1, -1), 2),
            ('RIGHTPADDING', (0, 0), (-1, -1), 2),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]))
    else:
        # No Logo: 1 Column (Info Centered)
        header_data = [[co_info]]
        header_table = Table(header_data, colWidths=[avail_width])
        header_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BOX', (0, 0), (-1, -1), 1, colors.black),
            ('LEFTPADDING', (0, 0), (-1, -1), 2),
            ('RIGHTPADDING', (0, 0), (-1, -1), 2),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]))
        
    elements.append(header_table)
    
    # --- Main Grid ---
    col_width = avail_width / 4.0
    
    main_data = []
    
    # Row 1: Payslip Month Title
    month_str = payslip_data.get('month', '')
    try:
        period_obj = datetime.strptime(month_str, '%Y-%m')
        period_text = period_obj.strftime('%B %Y')
        import calendar
        last_day = calendar.monthrange(period_obj.year, period_obj.month)[1]
        date_range = f"({period_obj.strftime('01-%m-%Y')} to {period_obj.strftime(f'{last_day}-%m-%Y')})"
    except:
        period_text = month_str
        date_range = ""
        
    title_text = f"Pay slip for the Month of {period_text} {date_range}"
    main_data.append([Paragraph(f"<b>{title_text}</b>", style_center), '', '', ''])
    
    # Employee Details Rows
    doj = payslip_data.get('joining_date')
    doj_str = doj.strftime('%d-%m-%Y') if doj else '-'
    att = payslip_data.get('attendance', {})
    
    def lbl(text): return Paragraph(f"{text}", style_left) # Regular weight for labels
    def val(text): return Paragraph(f"{text}", style_left) # Regular weight for values
    
    # Row 2
    main_data.append([lbl("Employee Name"), val(payslip_data.get('employee_name', '-')), lbl("Employee Code"), val(payslip_data.get('employee_code', '-'))])
    # Row 3
    main_data.append([lbl("Department"), val(payslip_data.get('department', '-')), lbl("Designation"), val(payslip_data.get('designation', '-'))])
    # Row 4
    main_data.append([lbl("Working Days"), val(str(att.get('working_days', 0))), lbl("Net Payable Days"), val(str(att.get('present_days', 0) + att.get('paid_leave_days', 0)))])
    # Row 5
    main_data.append([lbl("Date of Joining"), val(doj_str), lbl("PF UAN"), val(payslip_data.get('uan_no', '-'))])
    # Row 6
    main_data.append([lbl("Pay Mode"), val("Bank Transfer"), lbl("Bank Name"), val(payslip_data.get('bank_name', '-'))])
    # Row 7
    main_data.append([lbl("Location"), val(template_data.get('company_address', '').split(',')[0]), lbl("Account No."), val(payslip_data.get('account_no', '-'))])
    
    # Earnings & Deductions Header
    main_data.append([
        Paragraph("<b>Gross Salary</b>", style_bold_left), Paragraph("<b>Amount (Rs.)</b>", style_bold_right),
        Paragraph("<b>Deductions</b>", style_bold_left), Paragraph("<b>Amount (Rs.)</b>", style_bold_right)
    ])
    
    # Earnings & Deductions Data
    earnings = []
    deductions = []
    field_values = payslip_data.get('field_values', {})
    
    if field_config:
        fields = field_config.fields.filter(is_deleted=False).order_by('display_order')
        for field in fields:
            if not field.is_visible: continue
            val_amount = field_values.get(field.field_code, 0)
            formatted_val = f"{val_amount:,.2f}" if val_amount else "0.00"
            if field.field_type == 'EARNING':
                earnings.append((field.field_name, formatted_val))
            elif field.field_type == 'DEDUCTION':
                deductions.append((field.field_name, formatted_val))
    else:
        if payslip_data.get('gross_salary'):
             earnings.append(('Basic Salary', f"{payslip_data.get('gross_salary', 0):,.2f}"))
             
    # Fill empty rows to balance or at least show some space
    min_rows = 5
    max_rows = max(len(earnings), len(deductions), min_rows)
    
    for i in range(max_rows):
        row = []
        # Earning
        if i < len(earnings):
            row.append(lbl(earnings[i][0]))
            row.append(Paragraph(earnings[i][1], style_right))
        else:
            row.append('')
            row.append('')
            
        # Deduction
        if i < len(deductions):
            row.append(lbl(deductions[i][0]))
            row.append(Paragraph(deductions[i][1], style_right))
        else:
            row.append('')
            row.append('')
        main_data.append(row)
        
    # Totals Row
    total_earnings = payslip_data.get('total_earnings', 0)
    total_deductions = payslip_data.get('total_deductions', 0)
    
    main_data.append([
        Paragraph("<b>Gross Salary (A)</b>", style_bold_left), Paragraph(f"<b>{total_earnings:,.2f}</b>", style_bold_right),
        Paragraph("<b>Deductions Total (B)</b>", style_bold_left), Paragraph(f"<b>{total_deductions:,.2f}</b>", style_bold_right)
    ])
    
    # Employer Contribution (Optional - Placeholder to match Hitasoft if needed, but we don't have data)
    # Hitasoft has "Employer's Contribution (C)" and "CTC (A+C)". We'll skip if no data.
    
    # Net Pay Row
    net_pay = payslip_data.get('net_pay', 0)
    main_data.append([
        Paragraph("<b>Net Pay (A - B)</b>", style_bold_left), '', '', Paragraph(f"<b>{net_pay:,.2f}</b>", style_bold_right)
    ])
    
    # Amount in Words
    amount_words = ""
    try:
        from num2words import num2words
        amount_words = num2words(net_pay, lang='en_IN').title() + " Only"
    except ImportError:
        pass
        
    main_data.append([lbl("In Words"), Paragraph(f"<b>{amount_words}</b>", style_left), '', ''])

    # CL Balance
    cl_balance = payslip_data.get('cl_balance', '-')
    if cl_balance == '-':
        cl_balance = att.get('cl_balance', '-')
        
    main_data.append([lbl("CL Balance"), val(str(cl_balance)), '', ''])
    
    # Signatures
    main_data.append(['', '', '', '']) # Spacer
    main_data.append([
        Paragraph("<br/><b>Employee Signature</b>", style_center), '',
        '', Paragraph("<br/><b>Employer Signature</b>", style_center)
    ])

    # Build Table
    t = Table(main_data, colWidths=[col_width]*4)
    
    # Styles
    t_style = [
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black), # Thinner grid lines
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 1), # Very tight padding
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        
        # Title Row
        ('SPAN', (0, 0), (-1, 0)),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        
        # Net Pay Row Merges
        ('SPAN', (0, -3), (2, -3)), # Net Pay Label spans 3 cols
        
        # In Words Row
        ('SPAN', (1, -2), (-1, -2)), # Words value spans rest
        
        # Signatures
        ('SPAN', (0, -1), (1, -1)), # Emp Sig spans 2 cols
        ('SPAN', (2, -1), (3, -1)), # Employer Sig spans 2 cols
        ('ALIGN', (0, -1), (-1, -1), 'CENTER'),
        ('VALIGN', (0, -1), (-1, -1), 'BOTTOM'),
        ('TOPPADDING', (0, -1), (-1, -1), 10), # More padding for signatures
    ]
    
    t.setStyle(TableStyle(t_style))
    elements.append(t)
    
    # Footer
    elements.append(Spacer(1, 0.1*inch))
    footer_text = template_data.get('footer_text', 'This is a system generated payslip.')
    elements.append(Paragraph(footer_text, ParagraphStyle('Footer', parent=style_center, fontSize=7)))

    doc.build(elements)
    pdf_buffer.seek(0)
    return pdf_buffer
