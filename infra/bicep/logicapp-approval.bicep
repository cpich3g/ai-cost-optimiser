param location string = resourceGroup().location
param logicAppName string = 'la-approval-${uniqueString(resourceGroup().id)}'
param o365ConnectionName string
param callbackApiBaseUrl string
@secure()
param callbackToken string

resource office365Connection 'Microsoft.Web/connections@2016-06-01' = {
  name: o365ConnectionName
  location: location
  properties: {
    displayName: 'Office 365 Connection'
    api: {
      id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'office365')
    }
  }
}

resource logicApp 'Microsoft.Logic/workflows@2019-05-01' = {
  name: logicAppName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    state: 'Enabled'
    definition: loadJsonContent('../../logicapps/approval-workflow.json')
    parameters: {
      '$connections': {
        value: {
          office365: {
            connectionId: office365Connection.id
            connectionName: office365Connection.name
            id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'office365')
          }
        }
      }
      callbackApiBaseUrl: {
        value: callbackApiBaseUrl
      }
      callbackToken: {
        value: callbackToken
      }
    }
  }
}

output logicAppResourceId string = logicApp.id
output logicAppPrincipalId string = logicApp.identity.principalId
