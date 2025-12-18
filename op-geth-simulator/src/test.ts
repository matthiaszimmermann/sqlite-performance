const SERVER_URL = process.env.SERVER_URL || 'http://localhost:3000';

interface EntityWriteRequest {
  key: string;
  expiresIn: number; // Number of blocks from current block
  payload?: string;
  contentType: string;
  ownerAddress: string;
  deleted?: boolean;
  stringAnnotations?: Record<string, string>;
  numericAnnotations?: Record<string, number>;
}

async function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function writeEntity(entity: EntityWriteRequest): Promise<boolean> {
  try {
    const response = await fetch(`${SERVER_URL}/entities`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(entity),
    });

    if (!response.ok) {
      const error = await response.text();
      console.error(`✗ Failed to write entity ${entity.key}: ${response.status} ${error}`);
      return false;
    }

    const result = await response.json() as { queueSize: number };
    console.log(`✓ Queued entity: ${entity.key} (queue size: ${result.queueSize})`);
    return true;
  } catch (error) {
    console.error(`✗ Error writing entity ${entity.key}:`, error);
    return false;
  }
}

async function getEntity(key: string): Promise<unknown> {
  try {
    const response = await fetch(`${SERVER_URL}/entities/${encodeURIComponent(key)}`);

    if (response.status === 404) {
      console.log(`✗ Entity not found: ${key}`);
      return null;
    }

    if (!response.ok) {
      const error = await response.text();
      console.error(`✗ Failed to get entity ${key}: ${response.status} ${error}`);
      return null;
    }

    const entity = await response.json();
    console.log(`✓ Retrieved entity: ${key}`);
    return entity;
  } catch (error) {
    console.error(`✗ Error getting entity ${key}:`, error);
    return null;
  }
}

async function queryEntities(query: {
  ownerAddress?: string;
  stringAnnotations?: Record<string, string>;
  numericAnnotations?: Record<string, number>;
  limit?: number;
  offset?: number;
}): Promise<unknown[]> {
  try {
    const response = await fetch(`${SERVER_URL}/entities/query`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(query),
    });

    if (!response.ok) {
      const error = await response.text();
      console.error(`✗ Failed to query entities: ${response.status} ${error}`);
      return [];
    }

    const result = await response.json() as { count: number; entities: unknown[] };
    console.log(`✓ Query returned ${result.count} entities`);
    return result.entities;
  } catch (error) {
    console.error(`✗ Error querying entities:`, error);
    return [];
  }
}

async function countEntities(): Promise<number> {
  try {
    const response = await fetch(`${SERVER_URL}/entities/count`);

    if (!response.ok) {
      const error = await response.text();
      console.error(`✗ Failed to count entities: ${response.status} ${error}`);
      return 0;
    }

    const result = await response.json() as { count: number };
    console.log(`✓ Total entities in DB: ${result.count}`);
    return result.count;
  } catch (error) {
    console.error(`✗ Error counting entities:`, error);
    return 0;
  }
}

async function checkHealth(): Promise<boolean> {
  try {
    const response = await fetch(`${SERVER_URL}/health`);
    if (!response.ok) {
      console.error(`✗ Server health check failed: ${response.status}`);
      return false;
    }
    const health = await response.json() as { status: string; queueSize: number };
    console.log(`✓ Server health: ${health.status}, queue size: ${health.queueSize}`);
    return true;
  } catch (error) {
    console.error(`✗ Error checking server health:`, error);
    return false;
  }
}

async function runTests(): Promise<void> {
  console.log('🚀 Starting test script...\n');
  console.log(`Server URL: ${SERVER_URL}\n`);

  // Health check
  console.log('1. Health Check');
  await checkHealth();
  console.log('');

  // Initial count
  console.log('2. Initial Entity Count');
  const initialCount = await countEntities();
  console.log('');

  // Write multiple entities
  console.log('3. Writing Entities');
  // Block time is 2 seconds, so convert seconds to blocks
  // 3600 seconds = 1800 blocks, 7200 seconds = 3600 blocks, etc.
  const BLOCK_TIME_SECONDS = 2;
  
  // Entity 1: Simple entity (expires in 1 hour = 1800 blocks)
  await writeEntity({
    key: 'test-entity-1',
    expiresIn: 3600 / BLOCK_TIME_SECONDS, // 1800 blocks
    payload: Buffer.from('Hello, World!').toString('base64'),
    contentType: 'text/plain',
    ownerAddress: '0x1234567890123456789012345678901234567890',
    stringAnnotations: {
      tag: 'test',
      category: 'sample',
    },
    numericAnnotations: {
      priority: 1,
      version: 1,
    },
  });

  // Entity 2: Different owner (expires in 2 hours = 3600 blocks)
  await writeEntity({
    key: 'test-entity-2',
    expiresIn: 7200 / BLOCK_TIME_SECONDS, // 3600 blocks
    payload: Buffer.from('{"name": "Test", "value": 42}').toString('base64'),
    contentType: 'application/json',
    ownerAddress: '0xabcdefabcdefabcdefabcdefabcdefabcdefabcd',
    stringAnnotations: {
      tag: 'important',
      category: 'data',
    },
    numericAnnotations: {
      priority: 5,
      version: 2,
    },
  });

  // Entity 3: Same owner as Entity 1 (expires in 30 minutes = 900 blocks)
  await writeEntity({
    key: 'test-entity-3',
    payload: Buffer.from('Hello, World!').toString('base64'),
    expiresIn: 1800 / BLOCK_TIME_SECONDS, // 900 blocks
    contentType: 'application/json',
    ownerAddress: '0x1234567890123456789012345678901234567890',
    stringAnnotations: {
      tag: 'test',
      category: 'sample',
    },
    numericAnnotations: {
      priority: 3,
    },
  });

  // Entity 4: No annotations (expires in 15 minutes = 450 blocks)
  await writeEntity({
    key: 'test-entity-4',
    payload: Buffer.from('Hello, World!').toString('base64'),
    expiresIn: 900 / BLOCK_TIME_SECONDS, // 450 blocks
    contentType: 'text/plain',
    ownerAddress: '0x9876543210987654321098765432109876543210',
    stringAnnotations: {},
    numericAnnotations: {},
  });

  
  // Wait a bit for block processing (entities are queued, need to wait for processing)
  console.log('4. Waiting for block processing (3 seconds)...');
  await sleep(3000);
  console.log('');

  // Try to read entities immediately (might not be processed yet)
  console.log('5. Reading Entities (first attempt - may not be processed yet)');
  await getEntity('test-entity-1');
  await getEntity('test-entity-2');
  console.log('');

  // Wait more to ensure processing
  console.log('6. Waiting additional time for block processing (3 seconds)...');
  await sleep(3000);
  console.log('');

  // Read entities after processing
  console.log('7. Reading Entities (after processing)');
  await getEntity('test-entity-1');
  await getEntity('test-entity-2');
  await getEntity('test-entity-3');
  await getEntity('test-entity-4');
  console.log('');

  // Query by owner address
  console.log('8. Query by Owner Address');
  const owner1Entities = await queryEntities({
    ownerAddress: '0x1234567890123456789012345678901234567890',
  });
  console.log(`   Found ${owner1Entities.length} entities for owner 1\n`);

  // Query by string annotation
  console.log('9. Query by String Annotation');
  const taggedEntities = await queryEntities({
    stringAnnotations: {
      tag: 'test',
    },
  });
  console.log(`   Found ${taggedEntities.length} entities with tag='test'\n`);

  // Query by numeric annotation
  console.log('10. Query by Numeric Annotation');
  const priorityEntities = await queryEntities({
    numericAnnotations: {
      priority: 5,
    },
  });
  console.log(`   Found ${priorityEntities.length} entities with priority=5\n`);

  // Query by multiple annotations
  console.log('11. Query by Multiple Annotations');
  const complexQuery = await queryEntities({
    stringAnnotations: {
      tag: 'test',
      category: 'sample',
    },
    numericAnnotations: {
      priority: 1,
    },
  });
  console.log(`   Found ${complexQuery.length} entities matching complex query\n`);

  // Final count
  console.log('12. Final Entity Count');
  const finalCount = await countEntities();
  console.log(`   Entities added: ${finalCount - initialCount}\n`);

  // Test query with limit and offset
  console.log('13. Query with Pagination');
  const page1 = await queryEntities({ limit: 2, offset: 0 });
  console.log(`   Page 1 (limit=2, offset=0): ${page1.length} entities`);
  const page2 = await queryEntities({ limit: 2, offset: 2 });
  console.log(`   Page 2 (limit=2, offset=2): ${page2.length} entities\n`);

  // Test non-existent entity
  console.log('14. Test Non-existent Entity');
  await getEntity('non-existent-entity');
  console.log('');

  console.log('✅ All tests completed!');
}

// Run tests
runTests().catch(error => {
  console.error('Fatal error:', error);
  // Don't exit with error code, just log
});

