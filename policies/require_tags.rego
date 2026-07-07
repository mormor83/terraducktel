package main

required_tags := {"Environment", "ManagedBy"}

deny[msg] {
    resource := input.resource_changes[_]
    tags := resource.change.after.tags
    tags != null
    required := required_tags[_]
    not tags[required]
    msg := sprintf("Resource %s missing required tag %q", [resource.address, required])
}
