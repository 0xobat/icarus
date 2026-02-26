import Ajv, { type ValidateFunction, type ErrorObject } from 'ajv';
import addFormats from 'ajv-formats';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

export interface ValidationResult {
  valid: boolean;
  errors: ErrorObject[] | null;
}

export type SchemaName = 'market-events' | 'execution-orders' | 'execution-results';

const SCHEMA_DIR = resolve(__dirname, '../../../shared/schemas');

const ajv = new Ajv({ allErrors: true, strict: false, validateSchema: false });
addFormats(ajv);

const validators = new Map<SchemaName, ValidateFunction>();

/** Load or retrieve a cached JSON schema validator by name. */
function getValidator(name: SchemaName): ValidateFunction {
  let validator = validators.get(name);
  if (!validator) {
    const schemaPath = resolve(SCHEMA_DIR, `${name}.schema.json`);
    const schema = JSON.parse(readFileSync(schemaPath, 'utf-8'));
    // Remove $id to avoid ajv caching conflicts across reloads
    const schemaWithoutId = Object.fromEntries(
      Object.entries(schema).filter(([key]) => key !== '$id'),
    );
    validator = ajv.compile(schemaWithoutId);
    validators.set(name, validator);
  }
  return validator;
}

/** Validate data against a named JSON schema. */
export function validate(schemaName: SchemaName, data: unknown): ValidationResult {
  const validator = getValidator(schemaName);
  const valid = validator(data);
  return {
    valid: valid as boolean,
    errors: valid ? null : (validator.errors ?? null),
  };
}

/** Validate data against a named JSON schema, throwing on failure. */
export function validateOrThrow(schemaName: SchemaName, data: unknown): void {
  const result = validate(schemaName, data);
  if (!result.valid) {
    const messages = (result.errors ?? [])
      .map((e) => `${e.instancePath || '/'}: ${e.message}`)
      .join('; ');
    throw new Error(`Schema validation failed (${schemaName}): ${messages}`);
  }
}
