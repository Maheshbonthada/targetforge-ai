@description('Storage account for raw/intermediate genomic files (FASTQ, BAM, VCF) and the persisted report artifacts. Private container, no public blob access, no shared-key access — the API and Batch pool talk to it exclusively via managed identity. See docs/compliance-checklist.md for why genomic data gets its own account rather than sharing one with anything else.')
param location string
param namePrefix string
param environmentTag string
param genomicFilesContainerName string = 'genomic-files'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  // Storage account names: <=24 chars, lowercase alphanumeric only, globally unique.
  name: replace('${namePrefix}st', '-', '')
  location: location
  tags: { environment: environmentTag, app: 'targetforge' }
  sku: { name: environmentTag == 'prod' ? 'Standard_ZRS' : 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false // forces Entra ID (managed identity) auth for every client
    networkAcls: {
      defaultAction: 'Allow' // tighten to 'Deny' + private endpoint before real human-subject sequencing data — see compliance-checklist.md
      bypass: 'AzureServices'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: { enabled: true, days: 14 }
    containerDeleteRetentionPolicy: { enabled: true, days: 14 }
  }
}

resource genomicFilesContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: genomicFilesContainerName
  properties: {
    publicAccess: 'None'
  }
}

// Raw FASTQ/BAM are large (multi-GB per sample) and only read hot during
// their own alignment/variant-calling run; once a run completes there is no
// reason to keep paying Hot-tier prices for them. Cool at 30 days, Archive at
// 90 — rehydrate from Archive (hours, not ms) only if a run needs to be
// reproduced. See docs/cost-model.md, "Where genomic storage cost actually goes."
resource lifecyclePolicy 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'tier-down-genomic-files'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: { blobTypes: ['blockBlob'], prefixMatch: ['${genomicFilesContainerName}/'] }
            actions: {
              baseBlob: {
                tierToCool: { daysAfterModificationGreaterThan: 30 }
                tierToArchive: { daysAfterModificationGreaterThan: 90 }
              }
            }
          }
        }
      ]
    }
  }
}

output storageAccountId string = storage.id
output storageAccountName string = storage.name
output blobEndpoint string = storage.properties.primaryEndpoints.blob
