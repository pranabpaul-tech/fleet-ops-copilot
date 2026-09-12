// Tenant-level Fabric private link. Confirmed against Microsoft's own ARM template
// in "Set up and use a tenant-level private link" (learn.microsoft.com/fabric/security):
// the resource type is genuinely Microsoft.PowerBI/privateLinkServicesForPowerBI even
// though it's provisioning access for Fabric — that's not a typo.
//
// One private endpoint here needs THREE DNS zones (analysis / pbidedicated /
// powerquery), which is why this doesn't reuse modules/private-endpoint.bicep.
//
// Prerequisite this module can't do for you: a Fabric admin must flip the tenant
// setting "Azure Private Link" to Enabled first (~15 min to propagate). See
// infra/README.md phase 0.

@description('Microsoft Entra tenant ID that owns the Fabric tenant.')
param tenantId string = tenant().tenantId

@description('Azure region for the private endpoint NIC.')
param location string

param vnetId string
param peSubnetId string

param privateLinkServiceName string = 'pl-fabric-tenant-fleetops'
param privateEndpointName string = 'pe-fabric-tenant-fleetops'

var dnsZoneNames = [
  'privatelink.analysis.windows.net'
  'privatelink.pbidedicated.windows.net'
  'privatelink.prod.powerquery.microsoft.com'
]

resource tenantPLS 'Microsoft.PowerBI/privateLinkServicesForPowerBI@2020-06-01' = {
  name: privateLinkServiceName
  location: 'global'
  properties: {
    tenantId: tenantId
  }
}

resource dnsZones 'Microsoft.Network/privateDnsZones@2024-06-01' = [for zoneName in dnsZoneNames: {
  name: zoneName
  location: 'global'
}]

resource dnsZoneLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [for (zoneName, i) in dnsZoneNames: {
  parent: dnsZones[i]
  name: 'link-${uniqueString(vnetId)}'
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnetId
    }
    registrationEnabled: false
  }
}]

resource pe 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: privateEndpointName
  location: location
  properties: {
    subnet: {
      id: peSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'plsc-fabric-tenant'
        properties: {
          privateLinkServiceId: tenantPLS.id
          // "Tenant" — capitalized, per the Azure portal's own subresource picker.
          groupIds: ['Tenant']
        }
      }
    ]
  }
}

resource dnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: pe
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [for (zoneName, i) in dnsZoneNames: {
      name: replace(zoneName, '.', '-')
      properties: {
        privateDnsZoneId: dnsZones[i].id
      }
    }]
  }
}

output privateLinkServiceId string = tenantPLS.id
output privateEndpointId string = pe.id
