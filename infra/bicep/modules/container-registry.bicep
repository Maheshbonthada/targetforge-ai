@description('Azure Container Registry. Basic tier — a pre-revenue team pushes a handful of images a week, not the volume Standard/Premium exist for. The API pulls via its managed identity (AcrPull role in main.bicep), no registry credentials anywhere.')
param location string
param namePrefix string
param environmentTag string

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: replace('${namePrefix}acr', '-', '')
  location: location
  tags: { environment: environmentTag, app: 'targetforge' }
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false // CI/CD and the app both authenticate via Entra identity, not the shared admin account
  }
}

output loginServer string = registry.properties.loginServer
output registryId string = registry.id
