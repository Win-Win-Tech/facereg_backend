from rest_framework import serializers
from .models import PayslipTemplate, PayslipFieldConfig, PayslipField, PayslipRecord
from regface.models import Location, User, Employee


class PayslipTemplateSerializer(serializers.ModelSerializer):
    location_id = serializers.PrimaryKeyRelatedField(
        source='location', queryset=Location.objects.filter(is_deleted=False)
    )
    location_name = serializers.CharField(source='location.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)

    class Meta:
        model = PayslipTemplate
        fields = [
            'id', 'location_id', 'location_name', 'company_logo', 'company_name',
            'company_address', 'company_email', 'company_phone', 'company_gstin',
            'header_text', 'header_color', 'header_alignment', 'footer_text',
            'footer_color', 'layout_config', 'page_size', 'orientation', 'font_size',
            'created_by', 'created_by_name', 'created_at', 'updated_at', 'is_deleted'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by']


class PayslipFieldConfigSerializer(serializers.ModelSerializer):
    location_id = serializers.PrimaryKeyRelatedField(
        source='location', queryset=Location.objects.filter(is_deleted=False)
    )
    location_name = serializers.CharField(source='location.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)
    fields_count = serializers.SerializerMethodField()

    class Meta:
        model = PayslipFieldConfig
        fields = [
            'id', 'location_id', 'location_name', 'config_name', 'description',
            'is_active', 'created_by', 'created_by_name', 'created_at', 'updated_at',
            'is_deleted', 'fields_count'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by']

    def get_fields_count(self, obj):
        return obj.fields.filter(is_deleted=False).count()


class PayslipFieldSerializer(serializers.ModelSerializer):
    field_config_id = serializers.PrimaryKeyRelatedField(
        source='field_config', queryset=PayslipFieldConfig.objects.filter(is_deleted=False), write_only=True, required=False
    )
    config_name = serializers.CharField(source='field_config.config_name', read_only=True)

    class Meta:
        model = PayslipField
        fields = [
            'id', 'field_config_id', 'config_name', 'field_name', 'field_code',
            'field_type', 'value_type', 'value', 'display_order', 'is_visible',
            'default_value', 'created_at', 'updated_at', 'is_deleted'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make field_code read-only if updating existing field
        if self.instance:
            self.fields['field_code'].read_only = True
    
    def validate_field_code(self, value):
        """Auto-generate field_code from field_name if not provided"""
        # If value is None or empty, try to get from initial_data
        if not value or (isinstance(value, str) and value.strip() == ''):
            # Try to get field_name from initial_data or validated_data
            field_name = None
            if hasattr(self, 'initial_data'):
                field_name = self.initial_data.get('field_name', '')
            if not field_name and hasattr(self, 'validated_data'):
                field_name = self.validated_data.get('field_name', '')
            
            if field_name:
                # Convert "Basic Salary" to "BASIC_SALARY"
                value = str(field_name).upper().replace(' ', '_').replace('-', '_')
                # Remove special characters, keep alphanumeric and underscores
                value = ''.join(c for c in value if c.isalnum() or c == '_')
                # Remove consecutive underscores
                while '__' in value:
                    value = value.replace('__', '_')
                value = value.strip('_')
        
        # Ensure uppercase
        if value:
            value = str(value).upper()
        return value
    
    def validate(self, attrs):
        """Validate field_code changes - check if referenced in calculations"""
        field_config = attrs.get('field_config') or (self.instance.field_config if self.instance else None)
        field_code = attrs.get('field_code')
        
        if not field_config:
            return attrs
        
        # For new fields, check if field_code already exists (excluding soft-deleted)
        if not self.instance and field_code:
            existing_field = PayslipField.objects.filter(
                field_config=field_config,
                field_code=field_code,
                is_deleted=False
            ).first()
            
            if existing_field:
                raise serializers.ValidationError({
                    'field_code': f'A field with code "{field_code}" already exists. Please use a different field code or restore the deleted field.'
                })
        
        # For updates, check if field_code changes and if referenced in calculations
        if self.instance and 'field_code' in attrs:
            # This should not happen if field_code is read_only, but double-check
            old_code = self.instance.field_code
            new_code = attrs.get('field_code', old_code)
            
            if old_code != new_code:
                # Check if new_code already exists (excluding soft-deleted and current field)
                existing_field = PayslipField.objects.filter(
                    field_config=self.instance.field_config,
                    field_code=new_code,
                    is_deleted=False
                ).exclude(id=self.instance.id).first()
                
                if existing_field:
                    raise serializers.ValidationError({
                        'field_code': f'A field with code "{new_code}" already exists. Please use a different field code.'
                    })
                
                # Check if any field references this code in calculations
                referencing_fields = PayslipField.objects.filter(
                    field_config=self.instance.field_config,
                    value_type='CALCULATION',
                    value__icontains=old_code,
                    is_deleted=False
                ).exclude(id=self.instance.id)
                
                if referencing_fields.exists():
                    field_names = [f.field_name for f in referencing_fields]
                    raise serializers.ValidationError({
                        'field_code': f'Cannot change field_code "{old_code}" to "{new_code}". It is referenced in calculations by: {", ".join(field_names)}. Delete and recreate the field if you need to change the code.'
                    })
        
        return attrs


class PayslipFieldBulkSerializer(serializers.Serializer):
    """Serializer for bulk creating/updating fields"""
    fields = PayslipFieldSerializer(many=True)


class PayslipFieldBulkUpdateSerializer(serializers.Serializer):
    """Serializer for bulk updating fields - requires id for each field"""
    fields = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=False
    )


class PayslipFieldBulkDeleteSerializer(serializers.Serializer):
    """Serializer for bulk deleting fields"""
    field_ids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
        min_length=1
    )


class PayslipRecordSerializer(serializers.ModelSerializer):
    employee_id = serializers.PrimaryKeyRelatedField(
        source='employee', queryset=Employee.objects.all(), write_only=True
    )
    employee_name = serializers.CharField(source='employee.name', read_only=True)
    field_config_id = serializers.PrimaryKeyRelatedField(
        source='field_config', queryset=PayslipFieldConfig.objects.filter(is_deleted=False), write_only=True
    )
    field_config_name = serializers.CharField(source='field_config.config_name', read_only=True)
    template_id = serializers.PrimaryKeyRelatedField(
        source='template', queryset=PayslipTemplate.objects.filter(is_deleted=False), write_only=True
    )
    generated_by_name = serializers.CharField(source='generated_by.name', read_only=True)
    approved_by_name = serializers.CharField(source='approved_by.name', read_only=True)

    class Meta:
        model = PayslipRecord
        fields = [
            'id', 'employee_id', 'employee_name', 'field_config_id', 'field_config_name',
            'template_id', 'month', 'present_days', 'absent_days', 'working_days',
            'gross_salary', 'total_earnings', 'total_deductions', 'net_pay',
            'field_values', 'status', 'notes', 'generated_on', 'generated_by',
            'generated_by_name', 'approved_on', 'approved_by', 'approved_by_name',
            'pdf_file'
        ]
        read_only_fields = [
            'id', 'generated_on', 'generated_by', 'approved_on', 'approved_by',
            'pdf_file', 'field_values', 'gross_salary', 'total_earnings',
            'total_deductions', 'net_pay'
        ]


class PayslipGenerationSerializer(serializers.Serializer):
    """Serializer for payslip generation request"""
    employee_id = serializers.IntegerField()
    month = serializers.CharField(max_length=7)  # YYYY-MM
    field_config_id = serializers.UUIDField(required=False, allow_null=True)
    template_id = serializers.UUIDField(required=False, allow_null=True)


class PayslipBulkGenerationSerializer(serializers.Serializer):
    """Serializer for bulk payslip generation"""
    employee_ids = serializers.ListField(child=serializers.IntegerField())
    month = serializers.CharField(max_length=7)
    field_config_id = serializers.UUIDField(required=False, allow_null=True)
    template_id = serializers.UUIDField(required=False, allow_null=True)

