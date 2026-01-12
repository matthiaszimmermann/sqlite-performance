package main

// Entity represents a complete entity with all its metadata
type Entity struct {
	Key                      string
	ExpiresAt                int64
	Payload                  []byte
	ContentType              string
	CreatedAtBlock           int64
	LastModifiedAtBlock      int64
	Deleted                  bool
	TransactionIndexInBlock  int
	OperationIndexInTransaction int
	OwnerAddress             string
	StringAnnotations        map[string]string
	NumericAnnotations       map[string]float64
}

// EntityWriteRequest represents a request to write an entity
type EntityWriteRequest struct {
	Key                string
	ExpiresIn          int64 // Number of blocks from current block until expiration
	Payload            string // base64 encoded or plain string
	ContentType        string
	Deleted            bool
	OwnerAddress       string
	StringAnnotations  map[string]string
	NumericAnnotations map[string]interface{} // Can be number or string for range queries
}

// EntityQueryRequest represents a query request
type EntityQueryRequest struct {
	StringAnnotations  map[string]string
	NumericAnnotations map[string]interface{} // number for equality, string for range (e.g., ">=8", "<=32")
	OwnerAddress       string
	Limit              int
	Offset             int
}

// PendingEntity extends Entity with a unique ID for queue tracking
type PendingEntity struct {
	ID string
	Entity
}

