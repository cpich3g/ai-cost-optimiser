param location string = resourceGroup().location

@description('Unique name for the Function App.')
param functionAppName string = 'func-costopt-${uniqueString(resourceGroup().id)}'

@description('Unique name for the Logic App workflow.')
param logicAppName string = 'la-approval-${uniqueString(resourceGroup().id)}'

@description('Office 365 API connection resource name used by Logic App.')
param o365ConnectionName string

@description('Token used by Logic App when calling approval callback endpoint.')
@secure()
param callbackToken string

@description('Set to true to deploy a new Azure AI Foundry (OpenAI) resource and GPT-4.1 model. Set to false if you already have one.')
param deployAIFoundry bool = true

@description('Azure OpenAI endpoint URL. Required when deployAIFoundry is false.')
param azureOpenAiEndpoint string = ''

@description('Azure OpenAI chat deployment name. Required when deployAIFoundry is false.')
param azureOpenAiChatDeploymentName string = 'gpt-4.1'

@description('Approval timeout in hours for orchestration logic.')
param approvalTimeoutHours int = 72

@description('Email address for daily digest recipient.')
param digestRecipientEmail string = ''

@description('Email address for engineering approval notifications.')
param engineeringEmail string = ''

@description('Email address for finance approval notifications.')
param financeEmail string = ''

var storageAccountName = toLower('st${uniqueString(resourceGroup().id, functionAppName)}')
var appServicePlanName = 'asp-${functionAppName}'
var appInsightsName = 'appi-${functionAppName}'
var aiAccountName = 'ai-costopt-${uniqueString(resourceGroup().id)}'
var callbackApiBaseUrl = 'https://${functionApp.properties.defaultHostName}/api'
var storageBlobDataOwnerRoleId = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
var storageQueueDataContributorRoleId = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
var storageTableDataContributorRoleId = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
var storageAccountContributorRoleId = '17d1049b-9a84-46fb-8f53-869881c3d3ab'
var cognitiveServicesOpenAiUserId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
#disable-next-line no-unused-vars
var readerRoleId = 'acdd72a7-3385-48ef-bd42-f606fba81ae7'
#disable-next-line BCP318
var resolvedOpenAiEndpoint = deployAIFoundry ? aiAccount.properties.endpoint : azureOpenAiEndpoint
var resolvedDeploymentName = deployAIFoundry ? 'gpt-4dot1' : azureOpenAiChatDeploymentName

// --- Optional: Azure AI Foundry (OpenAI) ---
resource aiAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' = if (deployAIFoundry) {
  name: aiAccountName
  location: location
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: aiAccountName
    publicNetworkAccess: 'Enabled'
  }
}

resource gpt41Deployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = if (deployAIFoundry) {
  parent: aiAccount
  name: 'gpt-4dot1'
  sku: {
    name: 'GlobalStandard'
    capacity: 30
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4.1'
      version: '2025-04-14'
    }
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    accessTier: 'Hot'
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    IngestionMode: 'ApplicationInsights'
  }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: appServicePlanName
  location: location
  sku: {
    name: 'B1'
    tier: 'Basic'
  }
  kind: 'linux'
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      minTlsVersion: '1.2'
      alwaysOn: true
      appSettings: [
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'AzureWebJobsStorage__accountName'
          value: storage.name
        }
        {
          name: 'AzureWebJobsStorage__credential'
          value: 'managedidentity'
        }
        {
          name: 'WEBSITE_RUN_FROM_PACKAGE'
          value: '1'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        {
          name: 'APPINSIGHTS_INSTRUMENTATIONKEY'
          value: appInsights.properties.InstrumentationKey
        }
        {
          name: 'AZURE_OPENAI_ENDPOINT'
          value: resolvedOpenAiEndpoint
        }
        {
          name: 'AZURE_OPENAI_CHAT_DEPLOYMENT_NAME'
          value: resolvedDeploymentName
        }
        {
          name: 'APPROVAL_TIMEOUT_HOURS'
          value: string(approvalTimeoutHours)
        }
        {
          name: 'APPROVAL_CALLBACK_SECRET'
          value: callbackToken
        }
        {
          name: 'DIGEST_RECIPIENT_EMAIL'
          value: digestRecipientEmail
        }
        {
          name: 'ENGINEERING_EMAIL'
          value: engineeringEmail
        }
        {
          name: 'FINANCE_EMAIL'
          value: financeEmail
        }
        {
          name: 'AZURE_SUBSCRIPTION_ID'
          value: subscription().subscriptionId
        }
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true'
        }
      ]
    }
  }
}

resource blobDataOwnerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionApp.id, storageBlobDataOwnerRoleId)
  scope: storage
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataOwnerRoleId)
  }
}

resource queueDataContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionApp.id, storageQueueDataContributorRoleId)
  scope: storage
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageQueueDataContributorRoleId)
  }
}

resource tableDataContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionApp.id, storageTableDataContributorRoleId)
  scope: storage
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageTableDataContributorRoleId)
  }
}

resource storageAccountContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionApp.id, storageAccountContributorRoleId)
  scope: storage
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageAccountContributorRoleId)
  }
}

// Grant Function App Cognitive Services OpenAI User on the AI account (when deployed)
resource openaiUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployAIFoundry) {
  name: guid(aiAccount.id, functionApp.id, cognitiveServicesOpenAiUserId)
  scope: aiAccount
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAiUserId)
  }
}

module approvalLogicApp './logicapp-approval.bicep' = {
  name: 'approval-logicapp'
  params: {
    location: location
    logicAppName: logicAppName
    o365ConnectionName: o365ConnectionName
    callbackApiBaseUrl: callbackApiBaseUrl
    callbackToken: callbackToken
  }
}

output functionAppName string = functionApp.name
output functionAppPrincipalId string = functionApp.identity.principalId
output functionAppUrl string = 'https://${functionApp.properties.defaultHostName}'
output logicAppResourceId string = approvalLogicApp.outputs.logicAppResourceId
output logicAppPrincipalId string = approvalLogicApp.outputs.logicAppPrincipalId
output openAiEndpoint string = resolvedOpenAiEndpoint
output openAiDeploymentName string = resolvedDeploymentName
