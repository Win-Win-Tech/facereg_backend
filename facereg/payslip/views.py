import logging
from calendar import monthrange
from decimal import Decimal
from datetime import datetime
import os
import time

from django.db import connection
from django.db.models import Q
from django.http import FileResponse, Http404
from django.conf import settings
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser

from regface.models import User, Employee, Location
from regface.views import AuthenticatedAPIView, is_superadmin
from .models import PayslipTemplate, PayslipFieldConfig, PayslipField, PayslipRecord
from .serializers import (
    PayslipTemplateSerializer,
    PayslipFieldConfigSerializer,
    PayslipFieldSerializer,
    PayslipFieldBulkSerializer,
    PayslipFieldBulkUpdateSerializer,
    PayslipFieldBulkDeleteSerializer,
    PayslipRecordSerializer,
    PayslipGenerationSerializer,
    PayslipBulkGenerationSerializer,
)
from .pdf_generator import generate_payslip_pdf

logger = logging.getLogger(__name__)


# ==================== Default Salary Config Template ====================

def get_default_salary_config_template():
    """
    Returns the default salary config template with basic earning, deduction, and information fields.
    This template will be used for locations that don't have a salary config.
    """
    return {
        'config_name': 'Default Salary Config',
        'description': 'Default salary configuration with basic earning, deduction, and information fields',
        'fields': [
            # Earning Fields
            {
                'field_name': 'Basic Salary',
                'field_code': 'BASIC',
                'field_type': 'EARNING',
                'value_type': 'PERCENTAGE',
                'value': '60',  # 60% of gross salary
                'display_order': 1,
                'is_visible': True,
            },
            {
                'field_name': 'House Rent Allowance',
                'field_code': 'HRA',
                'field_type': 'EARNING',
                'value_type': 'PERCENTAGE',
                'value': '20',  # 20% of gross salary
                'display_order': 2,
                'is_visible': True,
            },
            {
                'field_name': 'Transport Allowance',
                'field_code': 'TRANSPORT',
                'field_type': 'EARNING',
                'value_type': 'PERCENTAGE',
                'value': '10',  # 10% of gross salary
                'display_order': 3,
                'is_visible': True,
            },
            {
                'field_name': 'Medical Allowance',
                'field_code': 'MEDICAL',
                'field_type': 'EARNING',
                'value_type': 'PERCENTAGE',
                'value': '10',  # 10% of gross salary
                'display_order': 4,
                'is_visible': True,
            },
            # Deduction Fields
            {
                'field_name': 'Provident Fund',
                'field_code': 'PF',
                'field_type': 'DEDUCTION',
                'value_type': 'CALCULATION',
                'value': 'BASIC * 0.12',  # 12% of Basic Salary (calculated after BASIC is computed)
                'display_order': 5,
                'is_visible': True,
            },
            {
                'field_name': 'Professional Tax',
                'field_code': 'PT',
                'field_type': 'DEDUCTION',
                'value_type': 'FIXED',
                'value': '200',  # Fixed amount
                'display_order': 6,
                'is_visible': True,
            },
            {
                'field_name': 'Absent Deduction',
                'field_code': 'ABSENT_DEDUCTION',
                'field_type': 'DEDUCTION',
                'value_type': 'CALCULATION',
                'value': 'absent_days * deduction_per_day',  # Calculation based on absent days
                'display_order': 7,
                'is_visible': True,
            },
            {
                'field_name': 'Employee State Insurance',
                'field_code': 'ESI',
                'field_type': 'DEDUCTION',
                'value_type': 'PERCENTAGE',
                'value': '0.75',  # 0.75% of gross salary (employee contribution)
                'display_order': 8,
                'is_visible': True,
            },
            # Information Fields
            {
                'field_name': 'Present Days',
                'field_code': 'PRESENT_DAYS',
                'field_type': 'INFO',
                'value_type': 'FIXED',
                'value': 'present_days',  # Reference to attendance data
                'display_order': 9,
                'is_visible': True,
            },
            {
                'field_name': 'Absent Days',
                'field_code': 'ABSENT_DAYS',
                'field_type': 'INFO',
                'value_type': 'FIXED',
                'value': 'absent_days',  # Reference to attendance data
                'display_order': 10,
                'is_visible': True,
            },
            {
                'field_name': 'Employee ID',
                'field_code': 'EMP_ID',
                'field_type': 'INFO',
                'value_type': 'FIXED',
                'value': 'employee.employee_id',  # Reference to employee field
                'display_order': 11,
                'is_visible': True,
            },
            {
                'field_name': 'Department',
                'field_code': 'DEPT',
                'field_type': 'INFO',
                'value_type': 'FIXED',
                'value': 'employee.department',  # Reference to employee field
                'display_order': 12,
                'is_visible': True,
            },
            {
                'field_name': 'Designation',
                'field_code': 'DESIGNATION',
                'field_type': 'INFO',
                'value_type': 'FIXED',
                'value': 'employee.designation',  # Reference to employee field
                'display_order': 13,
                'is_visible': True,
            },
        ]
    }


def create_default_salary_config_for_location(location, created_by=None):
    """
    Creates a default salary config for a location if it doesn't already have one.
    
    Args:
        location: Location instance
        created_by: User instance (optional)
    
    Returns:
        tuple: (config_instance, created_boolean) - Returns the config and whether it was created
    """
    # Check if location already has a config
    existing_config = PayslipFieldConfig.objects.filter(
        location=location,
        is_deleted=False
    ).first()
    
    if existing_config:
        return existing_config, False
    
    # Get default template
    template = get_default_salary_config_template()
    
    # Create the config
    config = PayslipFieldConfig.objects.create(
        location=location,
        config_name=template['config_name'],
        description=template['description'],
        is_active=True,
        created_by=created_by
    )
    
    # Create all fields
    for field_data in template['fields']:
        PayslipField.objects.create(
            field_config=config,
            field_name=field_data['field_name'],
            field_code=field_data['field_code'],
            field_type=field_data['field_type'],
            value_type=field_data['value_type'],
            value=field_data['value'],
            display_order=field_data['display_order'],
            is_visible=field_data['is_visible']
        )
    
    return config, True


def create_default_salary_configs_for_all_locations(created_by=None):
    """
    Checks all locations and creates default salary configs for locations that don't have one.
    
    Args:
        created_by: User instance (optional)
    
    Returns:
        dict: {
            'total_locations': int,
            'locations_with_config': int,
            'locations_created': int,
            'created_configs': list of config names
        }
    """
    all_locations = Location.objects.filter(is_deleted=False)
    total_locations = all_locations.count()
    
    locations_with_config = 0
    locations_created = 0
    created_configs = []
    
    for location in all_locations:
        config, was_created = create_default_salary_config_for_location(location, created_by)
        if was_created:
            locations_created += 1
            created_configs.append(f"{config.config_name} - {location.name}")
        else:
            locations_with_config += 1
    
    return {
        'total_locations': total_locations,
        'locations_with_config': locations_with_config,
        'locations_created': locations_created,
        'created_configs': created_configs
    }


# ==================== PayslipTemplate APIs ====================

class PayslipTemplateListCreateView(AuthenticatedAPIView):
    parser_classes = (MultiPartParser, FormParser)

    def get(self, request):
        location_id = request.query_params.get('location_id')
        templates = PayslipTemplate.objects.filter(is_deleted=False)
        
        if request.user.role == User.Role.ADMIN:
            if not request.user.location_id:
                return Response(
                    {"detail": "Admin user is not assigned to a location."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            templates = templates.filter(location=request.user.location)
        elif location_id:
            templates = templates.filter(location_id=location_id)
        
        serializer = PayslipTemplateSerializer(templates, many=True)
        return Response(serializer.data)

    def post(self, request):
        if request.user.role == User.Role.ADMIN:
            if not request.user.location_id:
                return Response(
                    {"detail": "Admin user is not assigned to a location."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # Admin can only create for their location
            request.data['location_id'] = str(request.user.location_id)
        
        # Check if template already exists for location (including deleted ones)
        location_id = request.data.get('location_id')
        existing_template = PayslipTemplate.objects.filter(location_id=location_id).first()
        
        if existing_template:
            if not existing_template.is_deleted:
                return Response(
                    {"detail": "Payslip template already exists for this location. Update existing template instead."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            else:
                # Restore and update the deleted template
                serializer = PayslipTemplateSerializer(existing_template, data=request.data)
                if serializer.is_valid():
                    serializer.save(created_by=request.user, is_deleted=False)
                    return Response(serializer.data, status=status.HTTP_201_CREATED)
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        serializer = PayslipTemplateSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(created_by=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PayslipTemplateDetailView(AuthenticatedAPIView):
    parser_classes = (MultiPartParser, FormParser)

    def get_object(self, pk):
        try:
            return PayslipTemplate.objects.get(pk=pk, is_deleted=False)
        except PayslipTemplate.DoesNotExist:
            return None

    def get(self, request, pk):
        template = self.get_object(pk)
        if not template:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if request.user.role == User.Role.ADMIN and template.location_id != request.user.location_id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = PayslipTemplateSerializer(template)
        return Response(serializer.data)

    def patch(self, request, pk):
        template = self.get_object(pk)
        if not template:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if request.user.role == User.Role.ADMIN and template.location_id != request.user.location_id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        
        serializer = PayslipTemplateSerializer(template, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        template = self.get_object(pk)
        if not template:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if request.user.role == User.Role.ADMIN and template.location_id != request.user.location_id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        
        template.is_deleted = True
        template.save(update_fields=['is_deleted', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class PayslipTemplatePreviewView(AuthenticatedAPIView):
    def get(self, request, pk):
        try:
            template = PayslipTemplate.objects.get(pk=pk, is_deleted=False)
        except PayslipTemplate.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
            
        if request.user.role == User.Role.ADMIN and template.location_id != request.user.location_id:
            return Response(status=status.HTTP_403_FORBIDDEN)
            
        # Prepare dummy data for preview
        dummy_payslip_data = {
            'employee_name': 'John Doe',
            'employee_code': 'EMP001',
            'employee_email': 'john.doe@example.com',
            'designation': 'Software Engineer',
            'department': 'Engineering',
            'joining_date': datetime.now(),
            'month': datetime.now().strftime('%Y-%m'),
            'gross_salary': Decimal('50000.00'),
            'total_earnings': Decimal('60000.00'),
            'total_deductions': Decimal('5000.00'),
            'net_pay': Decimal('55000.00'),
            'net_pay': Decimal('55000.00'),
            'field_values': {}, # Will be populated dynamically
            'attendance': {
                'present_days': Decimal('22'),
                'absent_days': Decimal('0'),
                'paid_leave_days': Decimal('0'),
                'unpaid_leave_days': Decimal('0'),
                'holiday_count': 0,
                'weekoff_count': 8,
                'working_days': 22,
            },
            'uan_no': '100000000000',
            'pf_no': 'AB/CDE/0000000/000/0000000',
            'esi_no': '0000000000',
            'bank_name': 'HDFC Bank',
            'account_no': '1234567890',
            'pan_no': 'ABCDE1234F',
            'cl_balance': Decimal('1.5'),
        }
        
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
        
        # Fetch field config for the location
        field_config = PayslipFieldConfig.objects.filter(location=template.location, is_deleted=False).first()
        
        if field_config:
            # Populate dummy values for configured fields so they appear in preview
            dummy_values = {}
            for field in field_config.fields.filter(is_deleted=False):
                # Generate a dummy amount based on field type or name
                if field.field_type == 'EARNING':
                    if 'BASIC' in field.field_code: dummy_values[field.field_code] = 30000
                    elif 'HRA' in field.field_code: dummy_values[field.field_code] = 12000
                    else: dummy_values[field.field_code] = 5000
                elif field.field_type == 'DEDUCTION':
                    if 'PF' in field.field_code: dummy_values[field.field_code] = 3600
                    elif 'ESI' in field.field_code: dummy_values[field.field_code] = 500
                    else: dummy_values[field.field_code] = 1000
                elif field.field_type == 'INFO':
                    # Generate dummy values for INFO fields based on common field codes
                    if 'absent' in field.field_code.lower(): dummy_values[field.field_code] = 2
                    elif 'present' in field.field_code.lower(): dummy_values[field.field_code] = 28
                    elif 'leave' in field.field_code.lower() or 'lop' in field.field_code.lower(): dummy_values[field.field_code] = 1
                    elif 'paid_leave' in field.field_code.lower(): dummy_values[field.field_code] = 2
                    elif 'unpaid_leave' in field.field_code.lower(): dummy_values[field.field_code] = 1
                    else: dummy_values[field.field_code] = 0  # Default for other INFO fields
            
            dummy_payslip_data['field_values'] = dummy_values
            
            # Recalculate totals based on dummy values
            total_earnings = sum(v for k, v in dummy_values.items() if k in [f.field_code for f in field_config.fields.filter(field_type='EARNING')])
            total_deductions = sum(v for k, v in dummy_values.items() if k in [f.field_code for f in field_config.fields.filter(field_type='DEDUCTION')])
            dummy_payslip_data['total_earnings'] = Decimal(total_earnings)
            dummy_payslip_data['total_deductions'] = Decimal(total_deductions)
            dummy_payslip_data['net_pay'] = Decimal(total_earnings - total_deductions)
        
        try:
            pdf_buffer = generate_payslip_pdf(dummy_payslip_data, template_data, field_config=field_config)
            filename = f"template_preview_{template.id}.pdf"
            return FileResponse(pdf_buffer, as_attachment=False, filename=filename)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ==================== PayslipFieldConfig APIs ====================

class PayslipFieldConfigListCreateView(AuthenticatedAPIView):
    def get(self, request):
        location_id = request.query_params.get('location_id')
        configs = PayslipFieldConfig.objects.filter(is_deleted=False)
        
        if request.user.role == User.Role.ADMIN:
            if not request.user.location_id:
                return Response(
                    {"detail": "Admin user is not assigned to a location."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            configs = configs.filter(location=request.user.location)
        elif location_id:
            configs = configs.filter(location_id=location_id)
        
        serializer = PayslipFieldConfigSerializer(configs, many=True)
        return Response(serializer.data)

    def post(self, request):
        if request.user.role == User.Role.ADMIN:
            if not request.user.location_id:
                return Response(
                    {"detail": "Admin user is not assigned to a location."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            request.data['location_id'] = str(request.user.location_id)
        
        serializer = PayslipFieldConfigSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(created_by=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PayslipFieldConfigDetailView(AuthenticatedAPIView):
    def get_object(self, pk):
        try:
            return PayslipFieldConfig.objects.get(pk=pk, is_deleted=False)
        except PayslipFieldConfig.DoesNotExist:
            return None

    def get(self, request, pk):
        config = self.get_object(pk)
        if not config:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if request.user.role == User.Role.ADMIN and config.location_id != request.user.location_id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        
        serializer = PayslipFieldConfigSerializer(config)
        return Response(serializer.data)

    def patch(self, request, pk):
        config = self.get_object(pk)
        if not config:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if request.user.role == User.Role.ADMIN and config.location_id != request.user.location_id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        
        serializer = PayslipFieldConfigSerializer(config, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        config = self.get_object(pk)
        if not config:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if request.user.role == User.Role.ADMIN and config.location_id != request.user.location_id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        
        config.is_deleted = True
        config.save(update_fields=['is_deleted', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ==================== PayslipField APIs ====================

class PayslipFieldListCreateView(AuthenticatedAPIView):
    def get(self, request, config_id):
        try:
            config = PayslipFieldConfig.objects.get(pk=config_id, is_deleted=False)
        except PayslipFieldConfig.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        if request.user.role == User.Role.ADMIN and config.location_id != request.user.location_id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        
        fields = config.fields.filter(is_deleted=False).order_by('display_order')
        serializer = PayslipFieldSerializer(fields, many=True)
        return Response(serializer.data)

    def post(self, request, config_id):
        try:
            config = PayslipFieldConfig.objects.get(pk=config_id, is_deleted=False)
        except PayslipFieldConfig.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        if request.user.role == User.Role.ADMIN and config.location_id != request.user.location_id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        
        # Add field_config_id from URL parameter
        data = request.data.copy()
        data['field_config_id'] = str(config_id)
        
        # Auto-generate field_code if not provided
        if not data.get('field_code') or data.get('field_code', '').strip() == '':
            field_name = data.get('field_name', '')
            if field_name:
                # Convert "Basic Salary" to "BASIC_SALARY"
                field_code = field_name.upper().replace(' ', '_').replace('-', '_')
                field_code = ''.join(c for c in field_code if c.isalnum() or c == '_')
                while '__' in field_code:
                    field_code = field_code.replace('__', '_')
                field_code = field_code.strip('_')
                data['field_code'] = field_code
        
        # Ensure field_code is uppercase
        if data.get('field_code'):
            data['field_code'] = data['field_code'].upper()
        
        # Check if field with same field_code already exists (excluding soft-deleted)
        field_code = data.get('field_code')
        if field_code:
            existing_field = PayslipField.objects.filter(
                field_config=config,
                field_code=field_code,
                is_deleted=False
            ).first()
            
            if existing_field:
                return Response(
                    {
                        "non_field_errors": [
                            f"A field with code '{field_code}' already exists. Please use a different field code."
                        ]
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        serializer = PayslipFieldSerializer(data=data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PayslipFieldDetailView(AuthenticatedAPIView):
    def get_object(self, config_id, field_id):
        try:
            field = PayslipField.objects.get(pk=field_id, field_config_id=config_id, is_deleted=False)
            return field
        except PayslipField.DoesNotExist:
            return None

    def get(self, request, config_id, field_id):
        field = self.get_object(config_id, field_id)
        if not field:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        if request.user.role == User.Role.ADMIN and field.field_config.location_id != request.user.location_id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        
        serializer = PayslipFieldSerializer(field)
        return Response(serializer.data)

    def patch(self, request, config_id, field_id):
        field = self.get_object(config_id, field_id)
        if not field:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        if request.user.role == User.Role.ADMIN and field.field_config.location_id != request.user.location_id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        
        serializer = PayslipFieldSerializer(field, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, config_id, field_id):
        field = self.get_object(config_id, field_id)
        if not field:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        if request.user.role == User.Role.ADMIN and field.field_config.location_id != request.user.location_id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        
        field.is_deleted = True
        field.save(update_fields=['is_deleted', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class PayslipFieldBulkCreateView(AuthenticatedAPIView):
    def post(self, request, config_id):
        try:
            config = PayslipFieldConfig.objects.get(pk=config_id, is_deleted=False)
        except PayslipFieldConfig.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        if request.user.role == User.Role.ADMIN and config.location_id != request.user.location_id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        
        # Add field_config_id to each field before validation
        request_data = request.data.copy()
        if 'fields' in request_data:
            for field in request_data['fields']:
                field['field_config_id'] = str(config_id)
        
        serializer = PayslipFieldBulkSerializer(data=request_data)
        if serializer.is_valid():
            fields_data = serializer.validated_data['fields']
            created_fields = []
            failed_fields = []
            
            for idx, field_data in enumerate(fields_data):
                field_name = field_data.get('field_name', 'Unknown')
                error_reason = None
                
                try:
                    # Auto-generate field_code if not provided
                    if not field_data.get('field_code') or field_data.get('field_code').strip() == '':
                        if field_name:
                            # Convert "Basic Salary" to "BASIC_SALARY"
                            field_code = field_name.upper().replace(' ', '_').replace('-', '_')
                            field_code = ''.join(c for c in field_code if c.isalnum() or c == '_')
                            while '__' in field_code:
                                field_code = field_code.replace('__', '_')
                            field_code = field_code.strip('_')
                            field_data['field_code'] = field_code
                    
                    # Ensure field_code is uppercase
                    if field_data.get('field_code'):
                        field_data['field_code'] = field_data['field_code'].upper()
                    
                    # Check if field with same field_code already exists (excluding soft-deleted)
                    field_code = field_data.get('field_code')
                    if field_code:
                        existing_field = PayslipField.objects.filter(
                            field_config=config,
                            field_code=field_code,
                            is_deleted=False
                        ).first()
                        
                        if existing_field:
                            error_reason = f'Duplicate code: {field_code}'
                            failed_fields.append({
                                'field_name': field_name,
                                'field_code': field_code,
                                'reason': error_reason
                            })
                            continue
                    
                    # Create the field
                    field = PayslipField.objects.create(**field_data)
                    created_fields.append(field)
                    
                except Exception as e:
                    # Catch any other errors during creation
                    error_reason = str(e)[:100] if str(e) else 'Unknown error'
                    failed_fields.append({
                        'field_name': field_name,
                        'field_code': field_data.get('field_code', 'N/A'),
                        'reason': error_reason
                    })
                    continue
            
            # Prepare response
            created_count = len(created_fields)
            failed_count = len(failed_fields)
            
            result_data = {
                'created_count': created_count,
                'failed_count': failed_count,
                'total_count': len(fields_data),
                'created': PayslipFieldSerializer(created_fields, many=True).data if created_fields else [],
                'failed': failed_fields if failed_fields else []
            }
            
            # Return appropriate status code
            if failed_count > 0 and created_count > 0:
                # Partial success
                return Response(result_data, status=status.HTTP_207_MULTI_STATUS)
            elif failed_count > 0 and created_count == 0:
                # All failed
                return Response(result_data, status=status.HTTP_400_BAD_REQUEST)
            else:
                # All succeeded
                return Response(result_data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PayslipFieldBulkUpdateView(AuthenticatedAPIView):
    def put(self, request, config_id):
        return self.patch(request, config_id)
    
    def patch(self, request, config_id):
        try:
            config = PayslipFieldConfig.objects.get(pk=config_id, is_deleted=False)
        except PayslipFieldConfig.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        if request.user.role == User.Role.ADMIN and config.location_id != request.user.location_id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        
        serializer = PayslipFieldBulkUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        fields_data = serializer.validated_data['fields']
        updated_fields = []
        errors = []
        
        for index, field_data in enumerate(fields_data):
            if 'id' not in field_data:
                errors.append({
                    'index': index,
                    'error': 'Field id is required for bulk update'
                })
                continue
            
            field_id = field_data.pop('id')
            
            # Prevent field_code changes in bulk update
            if 'field_code' in field_data:
                errors.append({
                    'index': index,
                    'field_id': str(field_id),
                    'error': 'Cannot change field_code in bulk update. Field code is read-only after creation to prevent breaking calculations.'
                })
                continue
            
            try:
                field = PayslipField.objects.get(
                    pk=field_id,
                    field_config=config,
                    is_deleted=False
                )
            except PayslipField.DoesNotExist:
                errors.append({
                    'index': index,
                    'field_id': str(field_id),
                    'error': 'Field not found'
                })
                continue
            
            # Validate field data using PayslipFieldSerializer
            field_serializer = PayslipFieldSerializer(
                field,
                data=field_data,
                partial=True
            )
            if field_serializer.is_valid():
                field_serializer.save()
                updated_fields.append(field)
            else:
                errors.append({
                    'index': index,
                    'field_id': str(field_id),
                    'errors': field_serializer.errors
                })
        
        if errors:
            return Response({
                'updated': PayslipFieldSerializer(updated_fields, many=True).data,
                'errors': errors
            }, status=status.HTTP_207_MULTI_STATUS)
        
        result_serializer = PayslipFieldSerializer(updated_fields, many=True)
        return Response(result_serializer.data, status=status.HTTP_200_OK)


class PayslipFieldBulkDeleteView(AuthenticatedAPIView):
    def delete(self, request, config_id):
        try:
            config = PayslipFieldConfig.objects.get(pk=config_id, is_deleted=False)
        except PayslipFieldConfig.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        if request.user.role == User.Role.ADMIN and config.location_id != request.user.location_id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        
        serializer = PayslipFieldBulkDeleteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        field_ids = serializer.validated_data['field_ids']
        
        # Verify all fields belong to this config
        fields = PayslipField.objects.filter(
            pk__in=field_ids,
            field_config=config,
            is_deleted=False
        )
        
        if fields.count() != len(field_ids):
            found_ids = set(str(f.id) for f in fields)
            missing_ids = [str(fid) for fid in field_ids if str(fid) not in found_ids]
            return Response({
                'detail': 'Some fields were not found or do not belong to this config',
                'missing_field_ids': missing_ids
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Soft delete all fields
        deleted_count = fields.update(is_deleted=True)
        
        return Response({
            'detail': f'Successfully deleted {deleted_count} field(s)',
            'deleted_count': deleted_count,
            'deleted_field_ids': [str(f.id) for f in fields]
        }, status=status.HTTP_200_OK)


# ==================== PayslipRecord APIs ====================

class PayslipRecordListView(AuthenticatedAPIView):
    def get(self, request):
        max_retries = 2
        retry_count = 0
        
        while retry_count <= max_retries:
            try:
                # Ensure database connection is alive
                connection.ensure_connection()
                
                month = request.query_params.get('month')
                employee_id = request.query_params.get('employee_id')
                location_id = request.query_params.get('location_id')
                
                # Start with base queryset - use select_related only for essential relations
                # Limit select_related to avoid connection timeouts on large datasets
                records = PayslipRecord.objects.select_related('employee', 'field_config', 'template').all()
                
                if request.user.role == User.Role.ADMIN:
                    if not request.user.location_id:
                        return Response(
                            {"detail": "Admin user is not assigned to a location."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    # Admin sees payslips for employees in their location
                    records = records.filter(employee__location=request.user.location)
                elif location_id:
                    records = records.filter(employee__location_id=location_id)
                
                if month:
                    records = records.filter(month=month)
                if employee_id:
                    records = records.filter(employee_id=employee_id)
                
                # Order by month and employee name for consistent results
                records = records.order_by('-month', 'employee__name')
                
                # Serialize the queryset
                serializer = PayslipRecordSerializer(records, many=True)
                return Response(serializer.data)
                
            except Exception as e:
                error_msg = str(e)
                is_connection_error = 'Lost connection' in error_msg or '2013' in error_msg or 'OperationalError' in str(type(e).__name__)
                
                if is_connection_error and retry_count < max_retries:
                    retry_count += 1
                    logger.warning(f"Database connection error (attempt {retry_count}/{max_retries}): {error_msg}")
                    # Close the broken connection
                    try:
                        connection.close()
                    except:
                        pass
                    # Wait a bit before retrying
                    time.sleep(0.5)
                    continue
                else:
                    logger.error(f"Error in PayslipRecordListView: {error_msg}", exc_info=True)
                    if is_connection_error:
                        return Response(
                            {"detail": "Database connection error. Please try again."},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE
                        )
                    return Response(
                        {"detail": f"An error occurred while fetching payslip records: {error_msg}"},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )
        
        # If we exhausted retries
        return Response(
            {"detail": "Database connection error after multiple retries. Please try again later."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE
        )


class PayslipRecordDetailView(AuthenticatedAPIView):
    def get_object(self, pk):
        try:
            return PayslipRecord.objects.get(pk=pk)
        except PayslipRecord.DoesNotExist:
            return None

    def get(self, request, pk):
        record = self.get_object(pk)
        if not record:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        if request.user.role == User.Role.ADMIN:
            if record.employee.location_id != request.user.location_id:
                return Response(status=status.HTTP_403_FORBIDDEN)
        
        serializer = PayslipRecordSerializer(record)
        return Response(serializer.data)

    def patch(self, request, pk):
        record = self.get_object(pk)
        if not record:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        if request.user.role == User.Role.ADMIN:
            if record.employee.location_id != request.user.location_id:
                return Response(status=status.HTTP_403_FORBIDDEN)
        
        serializer = PayslipRecordSerializer(record, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        record = self.get_object(pk)
        if not record:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        if request.user.role == User.Role.ADMIN:
            if record.employee.location_id != request.user.location_id:
                return Response(status=status.HTTP_403_FORBIDDEN)
        
        record.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PayslipGenerateView(AuthenticatedAPIView):
    """Generate payslip for an employee"""
    def post(self, request):
        serializer = PayslipGenerationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        data = serializer.validated_data
        employee_id = data['employee_id']
        month = data['month']
        field_config_id = data.get('field_config_id')
        template_id = data.get('template_id')
        
        try:
            employee = Employee.objects.get(pk=employee_id)
        except Employee.DoesNotExist:
            return Response(
                {"detail": "Employee not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        if request.user.role == User.Role.ADMIN:
            if employee.location_id != request.user.location_id:
                return Response(status=status.HTTP_403_FORBIDDEN)
        
        # Get field config
        if not field_config_id:
            if not employee.payslip_field_config:
                return Response(
                    {"detail": "Employee does not have a payslip field configuration assigned."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            field_config = employee.payslip_field_config
        else:
            try:
                field_config = PayslipFieldConfig.objects.get(pk=field_config_id, is_deleted=False)
            except PayslipFieldConfig.DoesNotExist:
                return Response(
                    {"detail": "Field configuration not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
        
        # Get template
        location = employee.location
        if not location:
            return Response(
                {"detail": "Employee is not assigned to a location."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        if not template_id:
            try:
                template = PayslipTemplate.objects.get(location=location, is_deleted=False)
            except PayslipTemplate.DoesNotExist:
                return Response(
                    {"detail": "Payslip template not found for employee's location."},
                    status=status.HTTP_404_NOT_FOUND,
                )
        else:
            try:
                template = PayslipTemplate.objects.get(pk=template_id, is_deleted=False)
            except PayslipTemplate.DoesNotExist:
                return Response(
                    {"detail": "Payslip template not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
        
        # Check if payslip already exists and delete it (overwrite)
        existing_record = PayslipRecord.objects.filter(employee=employee, month=month).first()
        if existing_record:
            existing_record.delete()
        
        # Use calculation engine to calculate all field values
        from .calculation_engine import calculate_payslip_fields, CalculationError
        
        try:
            result = calculate_payslip_fields(employee, field_config, month)
            field_values = result['field_values']
            total_earnings = result['total_earnings']
            total_deductions = result['total_deductions']
            attendance = result['attendance']
            
            # Use gross_salary from calculation result (sum of earnings) or fallback to employee's gross
            gross_salary = result.get('gross_salary', employee.gross_salary or employee.base_salary or Decimal('0'))
            
            # Net Pay = Total Earnings - Total Deductions
            # In standard payslip format:
            # - Earnings show full amounts
            # - Deductions include LOP (for absent days), PF, ESI, etc.
            # - Net Pay = Gross Salary - All Deductions
            net_pay = total_earnings - total_deductions
            
            # Ensure net_pay is not negative (set to 0 if negative)
            if net_pay < 0:
                net_pay = Decimal('0')
            
            record = PayslipRecord.objects.create(
                employee=employee,
                field_config=field_config,
                template=template,
                month=month,
                present_days=attendance['present_days'],  # Decimal (28.5)
                absent_days=attendance['absent_days'],    # Decimal (1.5)
                paid_leave_days=attendance['paid_leave_days'],
                unpaid_leave_days=attendance['unpaid_leave_days'],
                holiday_count=attendance['holiday_count'],
                weekoff_count=attendance['weekoff_count'],
                working_days=attendance['working_days'],
                gross_salary=gross_salary,
                total_earnings=total_earnings,
                total_deductions=total_deductions,
                net_pay=net_pay,
                field_values=field_values,
                generated_by=request.user,
            )
        except CalculationError as e:
            return Response(
                {"detail": f"Calculation error: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error(f"Error generating payslip: {str(e)}")
            return Response(
                {"detail": f"Error generating payslip: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        
        # Optionally generate PDF
        try:
            from .pdf_generator import save_payslip_pdf
            save_payslip_pdf(record)
        except Exception as e:
            logger.warning(f"Failed to generate PDF for payslip {record.id}: {str(e)}")
            # Continue even if PDF generation fails
        
        serializer = PayslipRecordSerializer(record)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class PayslipBulkGenerateView(AuthenticatedAPIView):
    """Generate payslips for multiple employees"""
    def post(self, request):
        serializer = PayslipBulkGenerationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        data = serializer.validated_data
        employee_ids = data['employee_ids']
        month = data['month']
        field_config_id = data.get('field_config_id')
        template_id = data.get('template_id')
        
        employees = Employee.objects.filter(pk__in=employee_ids)
        
        if request.user.role == User.Role.ADMIN:
            employees = employees.filter(location=request.user.location)
        
        if not employees.exists():
            return Response(
                {"detail": "No valid employees found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        # Get shared field config and template if provided
        shared_field_config = None
        if field_config_id:
            try:
                shared_field_config = PayslipFieldConfig.objects.get(pk=field_config_id, is_deleted=False)
            except PayslipFieldConfig.DoesNotExist:
                return Response(
                    {"detail": "Field configuration not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
        
        shared_template = None
        if template_id:
            try:
                shared_template = PayslipTemplate.objects.get(pk=template_id, is_deleted=False)
            except PayslipTemplate.DoesNotExist:
                return Response(
                    {"detail": "Payslip template not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
        
        # Generate payslips for each employee
        from .calculation_engine import calculate_payslip_fields, CalculationError
        
        generated = []
        errors = []
        
        for employee in employees:
            # Check if payslip already exists and delete it (overwrite)
            existing_record = PayslipRecord.objects.filter(employee=employee, month=month).first()
            if existing_record:
                existing_record.delete()
            
            # Get field config
            if shared_field_config:
                emp_field_config = shared_field_config
            elif employee.payslip_field_config:
                emp_field_config = employee.payslip_field_config
            else:
                errors.append({
                    'employee_id': employee.id,
                    'employee_name': employee.name,
                    'error': "Employee does not have a payslip field configuration assigned"
                })
                continue
            
            # Get template
            emp_location = employee.location
            if not emp_location:
                errors.append({
                    'employee_id': employee.id,
                    'employee_name': employee.name,
                    'error': "Employee is not assigned to a location"
                })
                continue
            
            if shared_template:
                emp_template = shared_template
            else:
                try:
                    emp_template = PayslipTemplate.objects.get(location=emp_location, is_deleted=False)
                except PayslipTemplate.DoesNotExist:
                    errors.append({
                        'employee_id': employee.id,
                        'employee_name': employee.name,
                        'error': "Payslip template not found for employee's location"
                    })
                    continue
            
            # Calculate and create payslip
            try:
                result = calculate_payslip_fields(employee, emp_field_config, month)
                field_values = result['field_values']
                total_earnings = result['total_earnings']
                total_deductions = result['total_deductions']
                attendance = result['attendance']
                
                # Use gross_salary from calculation result (sum of earnings) or fallback to employee's gross
                gross_salary = result.get('gross_salary', employee.gross_salary or employee.base_salary or Decimal('0'))
                
                # Net Pay = Total Earnings - Total Deductions
                net_pay = total_earnings - total_deductions
                
                # Ensure net_pay is not negative (set to 0 if negative)
                if net_pay < 0:
                    net_pay = Decimal('0')
                
                record = PayslipRecord.objects.create(
                    employee=employee,
                    field_config=emp_field_config,
                    template=emp_template,
                    month=month,
                    present_days=attendance['present_days'],  # Decimal (28.5)
                    absent_days=attendance['absent_days'],    # Decimal (1.5)
                    paid_leave_days=attendance['paid_leave_days'],
                    unpaid_leave_days=attendance['unpaid_leave_days'],
                    holiday_count=attendance['holiday_count'],
                    weekoff_count=attendance['weekoff_count'],
                    working_days=attendance['working_days'],
                    gross_salary=gross_salary,
                    total_earnings=total_earnings,
                    total_deductions=total_deductions,
                    net_pay=net_pay,
                    field_values=field_values,
                    generated_by=request.user,
                )
                
                # Optionally generate PDF
                try:
                    from .pdf_generator import save_payslip_pdf
                    save_payslip_pdf(record)
                except Exception as e:
                    logger.warning(f"Failed to generate PDF for payslip {record.id}: {str(e)}")
                
                generated.append(record)
            except CalculationError as e:
                errors.append({
                    'employee_id': employee.id,
                    'employee_name': employee.name,
                    'error': f"Calculation error: {str(e)}"
                })
            except Exception as e:
                logger.error(f"Error generating payslip for {employee.name}: {str(e)}")
                errors.append({
                    'employee_id': employee.id,
                    'employee_name': employee.name,
                    'error': f"Error: {str(e)}"
                })
        
        response_data = {
            'generated_count': len(generated),
            'errors_count': len(errors),
            'generated': PayslipRecordSerializer(generated, many=True).data,
            'errors': errors
        }
        
        if errors and not generated:
            return Response(response_data, status=status.HTTP_400_BAD_REQUEST)
        elif errors:
            return Response(response_data, status=status.HTTP_207_MULTI_STATUS)
        else:
            return Response(response_data, status=status.HTTP_201_CREATED)


class PayslipApproveView(AuthenticatedAPIView):
    """Approve a payslip"""
    def post(self, request, pk):
        try:
            record = PayslipRecord.objects.get(pk=pk)
        except PayslipRecord.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        if request.user.role == User.Role.ADMIN:
            if record.employee.location_id != request.user.location_id:
                return Response(status=status.HTTP_403_FORBIDDEN)
        
        record.status = 'APPROVED'
        record.approved_by = request.user
        record.approved_on = datetime.now()
        record.save()
        
        serializer = PayslipRecordSerializer(record)
        return Response(serializer.data)


class PayslipDownloadView(AuthenticatedAPIView):
    """Download payslip PDF - generates on-demand if not exists"""
    def get(self, request, pk):
        try:
            record = PayslipRecord.objects.select_related('employee', 'template', 'field_config').get(pk=pk)
        except PayslipRecord.DoesNotExist:
            return Response(
                {"detail": "Payslip not found."},
                status=status.HTTP_404_NOT_FOUND
            )
        
        if request.user.role == User.Role.ADMIN:
            if record.employee.location_id != request.user.location_id:
                return Response(status=status.HTTP_403_FORBIDDEN)
        
        # Check if PDF file exists
        if record.pdf_file and record.pdf_file.name:
            try:
                # Use the file field directly - Django handles the file path
                file = record.pdf_file
                if file.storage.exists(file.name):
                    response = FileResponse(
                        file.open('rb'),
                        content_type='application/pdf'
                    )
                    # Sanitize filename for download
                    safe_filename = f"payslip-{record.month}-{record.employee.name.replace(' ', '_')}.pdf"
                    response['Content-Disposition'] = f'attachment; filename="{safe_filename}"'
                    return response
            except Exception as e:
                logger.warning(f"Error accessing existing PDF file: {e}, will regenerate")
        
        # PDF doesn't exist, generate it on-demand
        try:
            # Prepare payslip data for PDF generation
            payslip_data = {
                'id': str(record.id),
                'employee_name': record.employee.name,
                'employee_id': str(record.employee.id),
                'employee_email': record.employee.email or '',
                'month': record.month,
                'period': record.month,
                'gross_salary': float(record.gross_salary),
                'total_earnings': float(record.total_earnings),
                'total_deductions': float(record.total_deductions),
                'net_pay': float(record.net_pay),
                'present_days': record.present_days,
                'absent_days': record.absent_days,
                'working_days': record.working_days,
            }
            
            # Add field values from field_values JSON
            if record.field_values:
                payslip_data.update(record.field_values)
            
            # Prepare template data
            template_data = None
            if record.template:
                template_data = {
                    'company_name': record.template.company_name or '',
                    'company_address': record.template.company_address or '',
                    'company_email': record.template.company_email or '',
                    'company_phone': record.template.company_phone or '',
                    'header_text': record.template.header_text or 'PAYSLIP',
                    'footer_text': record.template.footer_text or 'Confidential - For Employee Use Only',
                    'header_color': record.template.header_color or '#1e40af',
                    'footer_color': record.template.footer_color or '#64748b',
                    'page_size': record.template.page_size or 'A4',
                    'orientation': record.template.orientation or 'portrait',
                    'font_size': record.template.font_size or 10,
                }
            
            # Generate PDF
            pdf_buffer = generate_payslip_pdf(payslip_data, template_data)
            
            # Save PDF to record
            from django.core.files.base import ContentFile
            filename = f"payslip_{record.month}_{record.employee.id}.pdf"
            record.pdf_file.save(filename, ContentFile(pdf_buffer.read()), save=True)
            
            # Return the PDF
            pdf_buffer.seek(0)
            response = FileResponse(
                pdf_buffer,
                content_type='application/pdf'
            )
            safe_filename = f"payslip-{record.month}-{record.employee.name.replace(' ', '_')}.pdf"
            response['Content-Disposition'] = f'attachment; filename="{safe_filename}"'
            return response
            
        except ImportError as e:
            logger.error(f"PDF generation failed - reportlab not installed: {e}")
            return Response(
                {"detail": "PDF generation is not available. Please install reportlab: pip install reportlab"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        except Exception as e:
            logger.error(f"Error generating PDF: {e}", exc_info=True)
            return Response(
                {"detail": f"Error generating PDF: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# ==================== Default Salary Config Management APIs ====================

class CreateDefaultSalaryConfigsView(AuthenticatedAPIView):
    """
    API endpoint to create default salary configs for all locations that don't have one.
    Only accessible by superadmin.
    """
    def post(self, request):
        if not is_superadmin(request.user):
            return Response(
                {"detail": "Only superadmin can create default configs for all locations."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
            result = create_default_salary_configs_for_all_locations(created_by=request.user)
            
            return Response({
                "message": f"Default salary configs created successfully. {result['locations_created']} new configs created.",
                "details": result
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"Error creating default salary configs: {e}", exc_info=True)
            return Response(
                {"detail": f"Error creating default salary configs: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class CreateDefaultSalaryConfigForLocationView(AuthenticatedAPIView):
    """
    API endpoint to create default salary config for a specific location.
    """
    def post(self, request):
        location_id = request.data.get('location_id')
        
        if not location_id:
            return Response(
                {"detail": "location_id is required."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check permissions
        if request.user.role == User.Role.ADMIN:
            if not request.user.location_id or str(request.user.location_id) != str(location_id):
                return Response(
                    {"detail": "You can only create configs for your own location."},
                    status=status.HTTP_403_FORBIDDEN
                )
        
        try:
            location = Location.objects.get(id=location_id, is_deleted=False)
        except Location.DoesNotExist:
            return Response(
                {"detail": "Location not found."},
                status=status.HTTP_404_NOT_FOUND
            )
        
        try:
            config, was_created = create_default_salary_config_for_location(
                location, 
                created_by=request.user
            )
            
            if was_created:
                serializer = PayslipFieldConfigSerializer(config)
                return Response({
                    "message": f"Default salary config created successfully for {location.name}.",
                    "config": serializer.data
                }, status=status.HTTP_201_CREATED)
            else:
                serializer = PayslipFieldConfigSerializer(config)
                return Response({
                    "message": f"Location {location.name} already has a salary config.",
                    "config": serializer.data
                }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"Error creating default salary config: {e}", exc_info=True)
            return Response(
                {"detail": f"Error creating default salary config: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
