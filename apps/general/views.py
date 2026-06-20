from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import serializers
from rest_framework.filters import OrderingFilter
from rest_framework.generics import ListAPIView

from apps.general.authorizations import HasStaffRole
from apps.general.models import AuditLog
from apps.users.constants import StaffRole


class AuditLogSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source='actor.username', read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = [
            'id', 'actor', 'actor_username', 'actor_staff_role',
            'action', 'resource_type', 'resource_id',
            'details', 'ip_address', 'created',
        ]
        read_only_fields = fields


class AuditLogFilter(DjangoFilterBackend):
    pass


class AuditLogListView(ListAPIView):
    """Read-only audit log list. Accessible by Super Admin and Admin only."""

    serializer_class = AuditLogSerializer
    permission_classes = [HasStaffRole]
    required_staff_roles = [StaffRole.SUPER_ADMIN, StaffRole.ADMIN]
    read_staff_roles = [StaffRole.SUPER_ADMIN, StaffRole.ADMIN]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['actor', 'action', 'resource_type', 'resource_id', 'actor_staff_role']
    ordering_fields = ['created']
    ordering = ['-created']

    def get_queryset(self):
        qs = AuditLog.objects.select_related('actor').all()

        date_after = self.request.query_params.get('date_after')
        date_before = self.request.query_params.get('date_before')
        if date_after:
            qs = qs.filter(created__date__gte=date_after)
        if date_before:
            qs = qs.filter(created__date__lte=date_before)

        return qs
