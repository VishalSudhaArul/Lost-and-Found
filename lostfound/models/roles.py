from django.db import models


class RolePermissions(models.Model):
    """Unmanaged holder for application-level permissions that are not tied
    to a single model's CRUD. Groups receive these in ``lostfound.roles``."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ("access_security_dashboard", "Can access the security dashboard"),
            ("review_claims", "Can review claims and verification results"),
            ("decide_claims", "Can approve or reject claims"),
            ("view_private_details", "Can view private identifying details"),
            ("manage_custody", "Can receive and transfer items in custody"),
            ("conduct_handover", "Can schedule and complete supervised handovers"),
            ("review_disposal", "Can review ageing items for disposal"),
            ("authorize_disposal", "Can authorize and record disposal"),
            ("manage_taxonomy", "Can manage categories and locations"),
            ("manage_retention", "Can configure retention and escalation"),
            ("manage_matching", "Can configure matching thresholds"),
            ("manage_users", "Can manage users and roles"),
            ("view_analytics", "Can view reports and analytics"),
            ("view_audit", "Can view the audit log"),
        ]
