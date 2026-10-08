import importlib.util
from pathlib import Path


_MODULE_PATH = Path(__file__).parents[1] / "scripts" / "production_preflight.py"
_SPEC = importlib.util.spec_from_file_location("production_preflight", _MODULE_PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_MODULE)
validate_rendered_production = _MODULE.validate_rendered_production
validate_terraform_values = _MODULE.validate_terraform_values
validate_helm_values = _MODULE.validate_helm_values


def _resource(kind, name, **payload):
    return {"apiVersion": "v1", "kind": kind, "metadata": {"name": name}, **payload}


def test_production_preflight_rejects_placeholders_and_unsafe_defaults():
    resources = [
        _resource(
            "ConfigMap",
            "researchforge-config",
            data={
                "RESEARCHFORGE_ENV": "production",
                "RESEARCHFORGE_AUTH_MODE": "production",
                "RESEARCHFORGE_ALLOW_MOCK_MODELS": "0",
                "RESEARCHFORGE_STORE_BACKEND": "postgres",
                "RESEARCHFORGE_JOB_QUEUE_BACKEND": "redis",
                "RESEARCHFORGE_SANDBOX_BACKEND": "local",
                "RESEARCHFORGE_NETWORK_ENABLED": "1",
                "RESEARCHFORGE_SANDBOX_NETWORK_ENABLED": "0",
                "RESEARCHFORGE_ARTIFACT_STORE_BACKEND": "s3",
                "RESEARCHFORGE_ARTIFACT_STORE_AUTO_CREATE_BUCKET": "0",
                "RESEARCHFORGE_CORS_ORIGINS": "https://example.invalid",
                "RESEARCHFORGE_GITHUB_REDIRECT_URI": "https://example.invalid/",
                "RESEARCHFORGE_ARTIFACT_STORE_SERVER_SIDE_ENCRYPTION": "AES256",
            },
        ),
    ]
    findings = validate_rendered_production(resources)
    assert any("placeholders" in item for item in findings)
    assert any("SANDBOX_BACKEND" in item for item in findings)
    assert any("ExternalSecret" in item for item in findings)
    assert any("ConfigMap/researchforge-config.data.RESEARCHFORGE_CORS_ORIGINS" in item for item in findings)


def test_production_preflight_accepts_concrete_helm_values():
    values = {
        "config": {
            "modelBaseUrl": "https://gateway.example.com/v1",
            "modelName": "coding-model",
                "researchModelName": "research-model",
                "artifactBucket": "researchforge-prod-artifacts",
                "artifactEndpointUrl": "https://s3.ap-southeast-1.amazonaws.com",
                "githubWebhookPublicUrl": "https://researchforge.example.com/api/v1/integrations/github/webhook",
                "githubRedirectUri": "https://researchforge.example.com/",
                "artifactServerSideEncryption": "AES256",
            },
        "runtime": {"sandboxImage": "registry.example.com/researchforge/sandbox:sha256-abc"},
        "database": {"backupBucket": "researchforge-prod-backups", "cnpg": {"enabled": True, "instances": 3}},
        "restoreDrill": {"enabled": True, "schedule": "0 5 1 * *"},
        "ingress": {"host": "researchforge.example.com", "clusterIssuer": "letsencrypt-prod", "tlsSecretName": "researchforge-tls"},
    }
    assert validate_helm_values(values) == []


def test_production_preflight_rejects_public_cluster_without_allowlist(tmp_path):
    values = tmp_path / "terraform.tfvars"
    values.write_text("cluster_endpoint_public_access = true\n", encoding="utf-8")
    assert validate_terraform_values(values, "Terraform")


def test_production_preflight_rejects_all_mutable_image_tags():
    values = {
        "config": {
            "modelBaseUrl": "https://gateway.example.com/v1",
            "modelName": "coding-model",
            "researchModelName": "research-model",
            "artifactBucket": "researchforge-prod-artifacts",
            "artifactEndpointUrl": "https://s3.example.com",
            "githubWebhookPublicUrl": "https://researchforge.example.com/api/v1/integrations/github/webhook",
            "githubRedirectUri": "https://researchforge.example.com/",
            "artifactServerSideEncryption": "AES256",
        },
        "runtime": {"sandboxImage": "registry.example.com/researchforge/sandbox:dev"},
        "database": {"backupBucket": "researchforge-prod-backups", "cnpg": {"enabled": True, "instances": 3}},
        "restoreDrill": {"enabled": True, "schedule": "0 5 1 * *"},
        "ingress": {"host": "researchforge.example.com", "clusterIssuer": "letsencrypt-prod", "tlsSecretName": "researchforge-tls"},
    }
    findings = validate_helm_values(values)
    assert any("immutable" in item for item in findings)


def test_production_preflight_rejects_insecure_callback_and_cors():
    values = {
        "config": {
            "corsOrigins": "http://researchforge.example.com",
            "modelBaseUrl": "https://gateway.example.com/v1",
            "modelName": "coding-model",
            "researchModelName": "research-model",
            "artifactBucket": "researchforge-prod-artifacts",
            "artifactEndpointUrl": "https://s3.example.com",
            "githubWebhookPublicUrl": "https://researchforge.example.com/wrong-path",
            "githubRedirectUri": "http://researchforge.example.com/",
            "artifactServerSideEncryption": "none",
        },
        "runtime": {"sandboxImage": "registry.example.com/researchforge/sandbox:sha256-abc"},
        "database": {"backupBucket": "researchforge-prod-backups", "cnpg": {"enabled": True, "instances": 3}},
        "restoreDrill": {"enabled": True, "schedule": "0 5 1 * *"},
        "ingress": {"host": "researchforge.example.com", "clusterIssuer": "letsencrypt-prod", "tlsSecretName": "researchforge-tls"},
    }
    findings = validate_helm_values(values)
    assert any("corsOrigins" in item for item in findings)
    assert any("githubWebhookPublicUrl" in item for item in findings)
    assert any("githubRedirectUri" in item for item in findings)
    assert any("artifactServerSideEncryption" in item for item in findings)


def test_production_preflight_rejects_plaintext_external_endpoints():
    values = {
        "config": {
            "modelBaseUrl": "http://model.internal/v1",
            "modelName": "coding-model",
            "researchModelName": "research-model",
            "artifactBucket": "researchforge-prod-artifacts",
            "artifactEndpointUrl": "http://s3.internal",
            "githubWebhookPublicUrl": "https://researchforge.example.com/api/v1/integrations/github/webhook",
            "githubRedirectUri": "https://researchforge.example.com/",
            "artifactServerSideEncryption": "AES256",
        },
        "runtime": {"sandboxImage": "registry.example.com/researchforge/sandbox:sha256-abc"},
        "database": {"backupBucket": "researchforge-prod-backups", "cnpg": {"enabled": True, "instances": 3}},
        "restoreDrill": {"enabled": True, "schedule": "0 5 1 * *"},
        "ingress": {"host": "researchforge.example.com", "clusterIssuer": "letsencrypt-prod", "tlsSecretName": "researchforge-tls"},
    }
    findings = validate_helm_values(values)
    assert any("modelBaseUrl" in item for item in findings)
    assert any("artifactEndpointUrl" in item for item in findings)


def test_production_preflight_rejects_credentials_embedded_in_urls():
    values = {
        "config": {
            "modelBaseUrl": "https://user:secret@gateway.example.com/v1",
            "modelName": "coding-model",
            "researchModelName": "research-model",
            "artifactBucket": "researchforge-prod-artifacts",
            "artifactEndpointUrl": "https://s3.example.com",
            "githubWebhookPublicUrl": "https://researchforge.example.com/api/v1/integrations/github/webhook",
            "githubRedirectUri": "https://researchforge.example.com/",
            "artifactServerSideEncryption": "AES256",
        },
        "runtime": {"sandboxImage": "registry.example.com/researchforge/sandbox:sha256-abc"},
        "database": {"backupBucket": "researchforge-prod-backups", "cnpg": {"enabled": True, "instances": 3}},
        "restoreDrill": {"enabled": True, "schedule": "0 5 1 * *"},
        "ingress": {"host": "researchforge.example.com", "clusterIssuer": "letsencrypt-prod", "tlsSecretName": "researchforge-tls"},
    }
    findings = validate_helm_values(values)
    assert any("modelBaseUrl" in item for item in findings)


def test_production_preflight_requires_high_availability_resources():
    resources = [
        _resource(
            "ConfigMap",
            "researchforge-config",
            data={
                "RESEARCHFORGE_ENV": "production",
                "RESEARCHFORGE_AUTH_MODE": "production",
                "RESEARCHFORGE_ALLOW_MOCK_MODELS": "0",
                "RESEARCHFORGE_STORE_BACKEND": "postgres",
                "RESEARCHFORGE_JOB_QUEUE_BACKEND": "redis",
                "RESEARCHFORGE_SANDBOX_BACKEND": "kubernetes",
                "RESEARCHFORGE_NETWORK_ENABLED": "1",
                "RESEARCHFORGE_SANDBOX_NETWORK_ENABLED": "0",
                "RESEARCHFORGE_ARTIFACT_STORE_BACKEND": "s3",
                "RESEARCHFORGE_ARTIFACT_STORE_AUTO_CREATE_BUCKET": "0",
                "RESEARCHFORGE_CORS_ORIGINS": "https://researchforge.example.com",
                "RESEARCHFORGE_ARTIFACT_STORE_BUCKET": "artifacts",
                "RESEARCHFORGE_ARTIFACT_STORE_ENDPOINT_URL": "https://s3.example.com",
                "RESEARCHFORGE_ARTIFACT_STORE_SERVER_SIDE_ENCRYPTION": "AES256",
                "RESEARCHFORGE_MODEL_BASE_URL": "https://model.example.com/v1",
                "RESEARCHFORGE_MODEL_NAME": "coding-model",
                "RESEARCHFORGE_GITHUB_REDIRECT_URI": "https://researchforge.example.com/",
                "RESEARCHFORGE_GITHUB_WEBHOOK_PUBLIC_URL": "https://researchforge.example.com/api/v1/integrations/github/webhook",
            },
        ),
    ]
    findings = validate_rendered_production(resources)
    assert any("Deployment/researchforge-api" in item for item in findings)
    assert any("PodDisruptionBudget/researchforge-api" in item for item in findings)
    assert any("Cluster/researchforge-db" in item for item in findings)
    assert any("ExternalSecret/researchforge-application-secrets" in item for item in findings)


def test_production_preflight_requires_tls_ingress_and_sandbox_policy():
    resources = [
        _resource(
            "ConfigMap",
            "researchforge-config",
            data={
                "RESEARCHFORGE_ENV": "production",
                "RESEARCHFORGE_AUTH_MODE": "production",
                "RESEARCHFORGE_ALLOW_MOCK_MODELS": "0",
                "RESEARCHFORGE_STORE_BACKEND": "postgres",
                "RESEARCHFORGE_JOB_QUEUE_BACKEND": "redis",
                "RESEARCHFORGE_SANDBOX_BACKEND": "kubernetes",
                "RESEARCHFORGE_NETWORK_ENABLED": "1",
                "RESEARCHFORGE_SANDBOX_NETWORK_ENABLED": "0",
                "RESEARCHFORGE_ARTIFACT_STORE_BACKEND": "s3",
                "RESEARCHFORGE_ARTIFACT_STORE_AUTO_CREATE_BUCKET": "0",
                "RESEARCHFORGE_CORS_ORIGINS": "https://researchforge.example.com",
                "RESEARCHFORGE_ARTIFACT_STORE_BUCKET": "artifacts",
                "RESEARCHFORGE_ARTIFACT_STORE_ENDPOINT_URL": "https://s3.example.com",
                "RESEARCHFORGE_ARTIFACT_STORE_SERVER_SIDE_ENCRYPTION": "AES256",
                "RESEARCHFORGE_MODEL_BASE_URL": "https://model.example.com/v1",
                "RESEARCHFORGE_MODEL_NAME": "coding-model",
                "RESEARCHFORGE_GITHUB_REDIRECT_URI": "https://researchforge.example.com/",
                "RESEARCHFORGE_GITHUB_WEBHOOK_PUBLIC_URL": "https://researchforge.example.com/api/v1/integrations/github/webhook",
            },
        ),
    ]
    findings = validate_rendered_production(resources)
    assert any("Ingress/researchforge" in item for item in findings)
    assert any("default-deny" in item for item in findings)


def test_production_preflight_rejects_mutable_images():
    values = {
        "config": {
            "modelBaseUrl": "https://gateway.example.com/v1",
            "modelName": "coding-model",
            "researchModelName": "research-model",
            "artifactBucket": "researchforge-prod-artifacts",
            "artifactEndpointUrl": "https://s3.example.com",
            "githubWebhookPublicUrl": "https://researchforge.example.com/api/v1/integrations/github/webhook",
            "githubRedirectUri": "https://researchforge.example.com/",
            "artifactServerSideEncryption": "AES256",
        },
        "api": {"image": {"repository": "registry.example.com/researchforge/api", "tag": "latest"}},
        "runtime": {"sandboxImage": "registry.example.com/researchforge/sandbox:sha256-abc"},
        "database": {"backupBucket": "researchforge-prod-backups"},
        "ingress": {"host": "researchforge.example.com", "clusterIssuer": "letsencrypt-prod", "tlsSecretName": "researchforge-tls"},
    }
    assert any("immutable" in item for item in validate_helm_values(values))
