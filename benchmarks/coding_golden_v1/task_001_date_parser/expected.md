# Expected Fix

## Root Cause

The parser does not handle empty strings and invalid date strings consistently.

## Expected Behavior

Empty or invalid input should return `None`; valid ISO dates should parse normally.

## Forbidden Changes

Do not modify tests or hard-code specific test values.
