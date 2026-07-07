package main

deny[msg] {
    resource := input.resource_changes[_]
    resource.change.actions[_] == "delete"
    not input.metadata.override_approved
    msg := sprintf("Destructive operation on %s requires explicit approval", [resource.address])
}
