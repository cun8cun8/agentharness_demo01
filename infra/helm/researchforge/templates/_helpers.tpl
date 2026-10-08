{{- define "researchforge.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "researchforge.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- include "researchforge.name" . }}
{{- end }}
{{- end }}

{{- define "researchforge.labels" -}}
app.kubernetes.io/name: {{ include "researchforge.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: researchforge
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
{{- end }}

{{- define "researchforge.workspaceClaim" -}}
{{- default (printf "%s-sandbox-workspace" (include "researchforge.fullname" .)) .Values.runtime.workspace.existingClaim }}
{{- end }}
