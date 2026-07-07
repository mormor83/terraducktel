package main

# conftest collects members of the `deny` set. Use the classic partial-set form
# `deny[msg] { ... }` — NOT `deny[msg] if { ... }`, which parses as a complete
# rule and is invisible to conftest.
deny[msg] {
    resource := input.resource_changes[_]
    resource.type == "aws_s3_bucket_public_access_block"
    after := resource.change.after
    after != null
    after.block_public_acls == false
    msg := sprintf("Public access must be blocked for %s", [resource.address])
}
