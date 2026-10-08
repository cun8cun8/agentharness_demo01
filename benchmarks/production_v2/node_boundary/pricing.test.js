import test from 'node:test';
import assert from 'node:assert/strict';
import { discount } from './pricing.js';
test('20 percent', () => assert.equal(discount(100, 20), 80));
test('zero', () => assert.equal(discount(100, 0), 100));
test('full', () => assert.equal(discount(100, 100), 0));
