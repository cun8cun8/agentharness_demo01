# Expected Fix

## Root Cause

Page count uses floor division and drops the partially filled final page.

## Expected Behavior

Page count should use ceiling division and should not return less than one page.
