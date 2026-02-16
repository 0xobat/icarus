import 'dotenv/config';

const SERVICE_NAME = 'ts-executor';

function log(event: string, message: string, extra?: Record<string, unknown>): void {
  console.log(JSON.stringify({
    timestamp: new Date().toISOString(),
    service: SERVICE_NAME,
    event,
    message,
    ...extra,
  }));
}

async function main(): Promise<void> {
  log('startup', 'TypeScript executor starting...');

  // TODO: Initialize Redis connection (INFRA-002)
  // TODO: Initialize Alchemy WebSocket listener (LISTEN-001)
  // TODO: Initialize transaction executor (EXEC-001)
  // TODO: Initialize Smart Wallet (EXEC-002)

  log('ready', 'TypeScript executor ready');

  // Keep process alive
  await new Promise<void>((resolve) => {
    const shutdown = () => {
      log('shutdown', 'TypeScript executor shutting down');
      resolve();
    };
    process.on('SIGTERM', shutdown);
    process.on('SIGINT', shutdown);
  });
}

main().catch((err) => {
  log('fatal_error', err instanceof Error ? err.message : String(err));
  process.exit(1);
});
