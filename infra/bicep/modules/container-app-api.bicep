@description('The API container app. System-assigned managed identity is granted RBAC on the Postgres-adjacent Key Vault secret, Storage, Azure OpenAI, and (once deployed) Azure Batch in main.bicep — no credential ever lives in an env var as plaintext.')
param location string
param namePrefix string
param environmentTag string
param containerAppsEnvironmentId string
param containerImage string
param containerRegistryLoginServer string
param keyVaultUri string
param databaseUrlSecretName string
param appInsightsConnectionString string
param aiProvider string
param openAiEndpoint string
param storageBlobEndpoint string
param batchAccountUrl string
param batchPoolId string
param entraTenantId string
param entraClientId string
param entraIssuer string
param entraAudience string
param corsAllowedOrigins string
param ncbiToolEmail string

// A pipeline run executes as a FastAPI BackgroundTask *after* the 202
// response is sent (see api/routers/runs.py) — it can run for several
// minutes (multiple live external API + LLM calls). Consumption-plan
// Container Apps can scale a replica down once it judges the app idle, which
// would kill an in-flight background task and strand its run at status
// "running" forever. Keeping one warm replica in prod removes that failure
// mode outright; dev/staging accept the (rare, low-stakes) risk to stay at
// $0 idle cost. The durable fix — move run execution off the request-serving
// replica entirely onto a queue-triggered Container Apps Job — is the
// documented next step before this matters at real scale; see
// docs/scaling-to-real-genomes.md.
var minReplicas = environmentTag == 'prod' ? 1 : 0
var maxReplicas = environmentTag == 'prod' ? 10 : 3

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-api'
  location: location
  tags: { environment: environmentTag, app: 'targetforge' }
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: containerAppsEnvironmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http2'
        allowInsecure: false
      }
      registries: [
        { server: containerRegistryLoginServer, identity: 'system' }
      ]
      secrets: [
        {
          name: 'database-url'
          keyVaultUrl: '${keyVaultUri}secrets/${databaseUrlSecretName}'
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: containerImage
          resources: {
            // 0.5 vCPU / 1Gi comfortably serves this workload — it's I/O-bound
            // (awaiting PubMed/DGIdb/ClinicalTrials.gov/Azure OpenAI, not local
            // compute). Watch p95 CPU in Application Insights and resize before
            // it's a bottleneck, not after.
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'ENVIRONMENT', value: environmentTag }
            { name: 'LOG_LEVEL', value: environmentTag == 'prod' ? 'INFO' : 'DEBUG' }
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'KEY_VAULT_URL', value: keyVaultUri }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
            { name: 'AI_PROVIDER', value: aiProvider }
            { name: 'AZURE_OPENAI_ENDPOINT', value: openAiEndpoint }
            { name: 'STORAGE_ACCOUNT_URL', value: storageBlobEndpoint }
            { name: 'BATCH_ACCOUNT_URL', value: batchAccountUrl }
            { name: 'BATCH_POOL_ID', value: batchPoolId }
            { name: 'NCBI_TOOL_EMAIL', value: ncbiToolEmail }
            { name: 'ENTRA_TENANT_ID', value: entraTenantId }
            { name: 'ENTRA_CLIENT_ID', value: entraClientId }
            { name: 'ENTRA_ISSUER', value: entraIssuer }
            { name: 'ENTRA_AUDIENCE', value: entraAudience }
            { name: 'AUTH_DISABLED_FOR_LOCAL_DEV', value: 'false' }
            { name: 'CORS_ALLOWED_ORIGINS', value: corsAllowedOrigins }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/healthz', port: 8000 }
              initialDelaySeconds: 5
              periodSeconds: 15
            }
            {
              type: 'Readiness'
              httpGet: { path: '/readyz', port: 8000 }
              initialDelaySeconds: 5
              periodSeconds: 10
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-concurrency'
            http: {
              // A pipeline run holds its HTTP request open only for the initial
              // 202-accepted POST; the actual work happens in a background task
              // (see api/routers/runs.py), so concurrency here tracks incoming
              // requests, not in-flight pipeline runs.
              metadata: { concurrentRequests: '50' }
            }
          }
        ]
      }
    }
  }
}

output principalId string = containerApp.identity.principalId
output fqdn string = containerApp.properties.configuration.ingress.fqdn
