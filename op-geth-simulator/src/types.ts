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

export interface EntityQueryRequest {
  stringAnnotations?: Record<string, string>;
  numericAnnotations?: Record<string, number>;
  ownerAddress?: string;
  limit?: number;
  offset?: number;
}

export interface PendingEntity extends Entity {
  id: string; // unique ID for this pending write
}

