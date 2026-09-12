@description('Location for the Fabric capacity.')
param location string

@description('Fabric capacity name. Must be lowercase alphanumeric only — no hyphens, no underscores.')
@minLength(3)
@maxLength(63)
param capacityName string = 'fleetopsf8'

@description('UPNs of the Fabric capacity administrators. At least one is required, and the account running setup/06_ops_agent.py should be one of them — the Operations Agent runs under its creator\'s delegated identity.')
param adminMembers array

resource capacity 'Microsoft.Fabric/capacities@2023-11-01' = {
  name: capacityName
  location: location
  sku: {
    name: 'F8'
    tier: 'Fabric'
  }
  properties: {
    administration: {
      members: adminMembers
    }
  }
}

output capacityId string = capacity.id
output capacityName string = capacity.name
