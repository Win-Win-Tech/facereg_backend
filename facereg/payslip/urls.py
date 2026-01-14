from django.urls import path
from .views import (
    PayslipTemplateListCreateView,
    PayslipTemplateDetailView,
    PayslipFieldConfigListCreateView,
    PayslipFieldConfigDetailView,
    PayslipFieldListCreateView,
    PayslipFieldDetailView,
    PayslipFieldBulkCreateView,
    PayslipFieldBulkUpdateView,
    PayslipFieldBulkDeleteView,
    PayslipRecordListView,
    PayslipRecordDetailView,
    PayslipGenerateView,
    PayslipBulkGenerateView,
    PayslipApproveView,
    PayslipDownloadView,
    PayslipTemplatePreviewView,
    CreateDefaultSalaryConfigsView,
    CreateDefaultSalaryConfigForLocationView,
)

urlpatterns = [
    # Payslip Template APIs
    path('payslip-templates/', PayslipTemplateListCreateView.as_view(), name='payslip-templates'),
    path('payslip-templates/<uuid:pk>/', PayslipTemplateDetailView.as_view(), name='payslip-template-detail'),
    path('payslip-templates/<uuid:pk>/preview/', PayslipTemplatePreviewView.as_view(), name='payslip-template-preview'),
    
    # Payslip Field Config APIs
    path('payslip-field-configs/', PayslipFieldConfigListCreateView.as_view(), name='payslip-field-configs'),
    path('payslip-field-configs/<uuid:pk>/', PayslipFieldConfigDetailView.as_view(), name='payslip-field-config-detail'),
    
    # Payslip Field APIs
    path('payslip-field-configs/<uuid:config_id>/fields/', PayslipFieldListCreateView.as_view(), name='payslip-fields'),
    path('payslip-field-configs/<uuid:config_id>/fields/<uuid:field_id>/', PayslipFieldDetailView.as_view(), name='payslip-field-detail'),
    path('payslip-field-configs/<uuid:config_id>/fields/bulk/', PayslipFieldBulkCreateView.as_view(), name='payslip-fields-bulk'),
    path('payslip-field-configs/<uuid:config_id>/fields/bulk-update/', PayslipFieldBulkUpdateView.as_view(), name='payslip-fields-bulk-update'),
    path('payslip-field-configs/<uuid:config_id>/fields/bulk-delete/', PayslipFieldBulkDeleteView.as_view(), name='payslip-fields-bulk-delete'),
    
    # Payslip Record APIs
    path('payslips/', PayslipRecordListView.as_view(), name='payslips'),
    path('payslips/generate/', PayslipGenerateView.as_view(), name='payslip-generate'),
    path('payslips/generate-bulk/', PayslipBulkGenerateView.as_view(), name='payslip-generate-bulk'),
    path('payslips/<uuid:pk>/download/', PayslipDownloadView.as_view(), name='payslip-download'),
    path('payslips/<uuid:pk>/approve/', PayslipApproveView.as_view(), name='payslip-approve'),
    path('payslips/<uuid:pk>/', PayslipRecordDetailView.as_view(), name='payslip-detail'),
    
    # Default Salary Config APIs
    path('payslip-field-configs/create-default-all/', CreateDefaultSalaryConfigsView.as_view(), name='create-default-salary-configs-all'),
    path('payslip-field-configs/create-default/', CreateDefaultSalaryConfigForLocationView.as_view(), name='create-default-salary-config'),
]

