"""Set E2B_API_URL, E2B_SANDBOX_URL and E2B_API_KEY before running."""
from e2b import Sandbox

sandbox = Sandbox.create('base', timeout=60)
try:
    result = sandbox.commands.run('echo sandbox-ok; uname -s')
    print(result.stdout, end='')
finally:
    sandbox.kill()
