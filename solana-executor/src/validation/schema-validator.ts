/**
 * AJV-backed schema validator scoped to the Solana executor's envelope set.
 *
 * Mirrors ts-executor/src/validation/schema-validator.ts. Both services read
 * the same shared/schemas/ directory so envelope shapes can never drift
 * between chains.
 */
import Ajv, { type ValidateFunction, type ErrorObject } from "ajv";
import addFormats from "ajv-formats";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

export interface ValidationResult {
  valid: boolean;
  errors: ErrorObject[] | null;
}

export type SchemaName =
  | "market-event"
  | "execution-order"
  | "execution-result";

// In Docker, shared/ is mounted at /app/shared (see docker-compose volumes).
// Locally, shared/ is three dirs up from src/validation/. SCHEMA_DIR env var
// overrides either resolution path (used by tests + non-standard layouts).
const SCHEMA_DIR =
  process.env.SCHEMA_DIR ??
  (() => {
    const dockerPath = resolve("/app/shared/schemas");
    const localPath = resolve(__dirname, "../../../shared/schemas");
    try {
      readFileSync(resolve(dockerPath, "execution-order.schema.json"));
      return dockerPath;
    } catch {
      return localPath;
    }
  })();

const ajv = new Ajv({ allErrors: true, strict: false, validateSchema: false });
addFormats(ajv);

const validators = new Map<SchemaName, ValidateFunction>();

function getValidator(name: SchemaName): ValidateFunction {
  let validator = validators.get(name);
  if (!validator) {
    const schemaPath = resolve(SCHEMA_DIR, `${name}.schema.json`);
    const schema = JSON.parse(readFileSync(schemaPath, "utf-8")) as Record<
      string,
      unknown
    >;
    // Drop $id to avoid AJV cache conflicts when validators get reloaded
    // (tests do this to pick up fixture variants).
    const schemaWithoutId = Object.fromEntries(
      Object.entries(schema).filter(([key]) => key !== "$id"),
    );
    validator = ajv.compile(schemaWithoutId);
    validators.set(name, validator);
  }
  return validator;
}

/**
 * Validate `data` against the named schema.
 * Returns {valid, errors}. Never throws on validation failure.
 */
export function validate(
  schemaName: SchemaName,
  data: unknown,
): ValidationResult {
  const validator = getValidator(schemaName);
  const valid = validator(data) as boolean;
  return {
    valid,
    errors: valid ? null : (validator.errors ?? null),
  };
}

/**
 * Validate `data` against the named schema; throw on failure.
 * Use only on the publisher side where invalid output is a programmer bug —
 * consumer side should call `validate` so bad envelopes can be ACKed and
 * dropped rather than crashing the loop.
 */
export function validateOrThrow(schemaName: SchemaName, data: unknown): void {
  const result = validate(schemaName, data);
  if (!result.valid) {
    const messages = (result.errors ?? [])
      .map((e) => `${e.instancePath || "/"}: ${e.message}`)
      .join("; ");
    throw new Error(`Schema validation failed (${schemaName}): ${messages}`);
  }
}
