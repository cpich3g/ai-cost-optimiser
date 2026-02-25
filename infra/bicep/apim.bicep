param location string = resourceGroup().location
param apimName string = 'apim-approval-${uniqueString(resourceGroup().id)}'
param publisherName string = 'Approval Platform Team'
param publisherEmail string = 'platform-team@contoso.com'
param backendServiceUrl string
@secure()
param approvalCallbackToken string

resource apim 'Microsoft.ApiManagement/service@2023-09-01-preview' = {
  name: apimName
  location: location
  sku: {
    name: 'Developer'
    capacity: 1
  }
  properties: {
    publisherName: publisherName
    publisherEmail: publisherEmail
  }
}

resource tokenNamedValue 'Microsoft.ApiManagement/service/namedValues@2023-09-01-preview' = {
  parent: apim
  name: 'approval-callback-token'
  properties: {
    displayName: 'approval-callback-token'
    value: approvalCallbackToken
    secret: true
  }
}

resource approvalApi 'Microsoft.ApiManagement/service/apis@2023-09-01-preview' = {
  parent: apim
  name: 'approval-callback-api'
  properties: {
    displayName: 'Approval Callback API'
    path: 'approvals'
    protocols: [
      'https'
    ]
    serviceUrl: backendServiceUrl
    subscriptionRequired: true
  }
}

resource callbackOperation 'Microsoft.ApiManagement/service/apis/operations@2023-09-01-preview' = {
  parent: approvalApi
  name: 'post-approval-callback'
  properties: {
    displayName: 'Post approval callback'
    method: 'POST'
    urlTemplate: '/callback'
    request: {
      description: 'Receives approval decisions from Logic App/O365 links.'
      representations: [
        {
          contentType: 'application/json'
        }
      ]
    }
    responses: [
      {
        statusCode: 200
        description: 'Accepted'
      }
      {
        statusCode: 401
        description: 'Unauthorized'
      }
    ]
  }
}

resource callbackPolicy 'Microsoft.ApiManagement/service/apis/operations/policies@2023-09-01-preview' = {
  parent: callbackOperation
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: '<policies><inbound><base /><choose><when condition="@(context.Request.Headers.GetValueOrDefault("x-approval-token", "") != "{{approval-callback-token}}")"><return-response><set-status code="401" reason="Unauthorized" /></return-response></when></choose></inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>'
  }
  dependsOn: [
    tokenNamedValue
  ]
}

output apimGatewayUrl string = 'https://${apimName}.azure-api.net/approvals/callback'