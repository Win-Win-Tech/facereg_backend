import logging
from calendar import monthrange
from decimal import Decimal
from datetime import datetime
import os

from django.db.models import Q
from django.http import FileResponse, Http404
from django.conf import settings
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

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


# ==================== PayslipTemplate APIs ====================

class PayslipTemplateListCreateView(AuthenticatedAPIView):
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
        
        # Check if template already exists for location
        location_id = request.data.get('location_id')
        if PayslipTemplate.objects.filter(location_id=location_id, is_deleted=False).exists():
            return Response(
                {"detail": "Payslip template already exists for this location. Update existing template instead."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        serializer = PayslipTemplateSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(created_by=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PayslipTemplateDetailView(AuthenticatedAPIView):
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
            for field_data in fields_data:
                # Auto-generate field_code if not provided
                if not field_data.get('field_code') or field_data.get('field_code').strip() == '':
                    field_name = field_data.get('field_name', '')
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
                
                # field_config is already set by serializer validation (from field_config_id)
                field = PayslipField.objects.create(**field_data)
                created_fields.append(field)
            
            result_serializer = PayslipFieldSerializer(created_fields, many=True)
            return Response(result_serializer.data, status=status.HTTP_201_CREATED)
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
        month = request.query_params.get('month')
        employee_id = request.query_params.get('employee_id')
        location_id = request.query_params.get('location_id')
        
        records = PayslipRecord.objects.all()
        
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
        
        serializer = PayslipRecordSerializer(records, many=True)
        return Response(serializer.data)


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
        
        # Check if payslip already exists
        if PayslipRecord.objects.filter(employee=employee, month=month).exists():
            return Response(
                {"detail": f"Payslip for {month} already exists for this employee."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # TODO: Implement calculation logic
        # For now, create a basic payslip record
        # This will be implemented with the calculation engine
        
        # Placeholder calculation (will be replaced with proper calculation engine)
        gross_salary = employee.gross_salary or employee.base_salary or Decimal('0')
        present_days = 0  # TODO: Calculate from attendance
        absent_days = 0   # TODO: Calculate from attendance
        working_days = 30  # TODO: Calculate based on month
        
        # TODO: Calculate field values using field_config.fields
        field_values = {}
        total_earnings = gross_salary
        total_deductions = Decimal('0')
        
        record = PayslipRecord.objects.create(
            employee=employee,
            field_config=field_config,
            template=template,
            month=month,
            present_days=present_days,
            absent_days=absent_days,
            working_days=working_days,
            gross_salary=gross_salary,
            total_earnings=total_earnings,
            total_deductions=total_deductions,
            net_pay=total_earnings - total_deductions,
            field_values=field_values,
            generated_by=request.user,
        )
        
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
        
        # TODO: Implement bulk generation with calculation engine
        # For now, return placeholder response
        return Response(
            {"detail": "Bulk generation will be implemented with calculation engine."},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )


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
