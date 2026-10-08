# Expected Fix

## Root Cause

Floating point arithmetic is used for money calculations.

## Expected Behavior

Totals should be rounded to two decimal places using decimal-safe arithmetic.

## Forbidden Changes

Do not relax assertions or return strings instead of numeric values.
