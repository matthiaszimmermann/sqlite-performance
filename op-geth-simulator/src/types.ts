export interface Entity {
  key: string;
  expiresAt: number;
  payload?: Buffer | string;
  contentType: string;
  createdAtBlock: number;
  lastModifiedAtBlock: number;
  deleted: boolean;
  transactionIndexInBlock: number;
  operationIndexInTransaction: number;
  ownerAddress: string;
  stringAnnotations?: Record<string, string>;
  numericAnnotations?: Record<string, number>;
}

export interface EntityWriteRequest {
  key: string;
  expiresIn: number; // Number of blocks from current block until expiration
  payload?: string; // base64 encoded or plain string
  contentType: string;
  deleted?: boolean;
  ownerAddress: string;
  stringAnnotations?: Record<string, string>;
  numericAnnotations?: Record<string, number>;
}

// Partial update to an existing entity. Only provided fields are applied.
// Implemented by enqueueing a new write for the same key with merged fields.
export interface EntityUpdateRequest {
  expiresIn?: number; // Number of blocks from current block until expiration
  payload?: string; // base64 encoded or plain string
  contentType?: string;
  deleted?: boolean;
  ownerAddress?: string;
  stringAnnotations?: Record<string, string>;
  numericAnnotations?: Record<string, number>;
}

export interface EntityQueryRequest {
  stringAnnotations?: Record<string, string>;
  numericAnnotations?: Record<string, number | string>; // number for equality, string for range (e.g., ">=8", "<=32", ">16", "<64", "!=0")
  ownerAddress?: string;
  limit?: number;
  offset?: number;
}

export interface PendingEntity extends Entity {
  id: string; // unique ID for this pending write
}

