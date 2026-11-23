if (process.env.MCEL_SCRAMJET_SMOKE === '1') {
  const {startFixture} = await import('./fixture.mjs');
  await startFixture();
}
await import('../src/index.js');
