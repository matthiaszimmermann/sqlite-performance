package main

// Entity represents a complete entity with all its metadata
type Entity struct {
	Key                         string
	ExpiresAt                   int64
	Payload                     []byte
	ContentType                 string
	CreatedAtBlock              int64
	LastModifiedAtBlock         int64
	Deleted                     bool
	TransactionIndexInBlock     int
	OperationIndexInTransaction int
	OwnerAddress                string
	StringAnnotations           map[string]string
	NumericAnnotations          map[string]float64
}

// EntityCreateRequest represents a request to create an entity.
type EntityCreateRequest struct {
	Key                string
	ExpiresIn          int64  // Number of blocks from current block until expiration
	Payload            string // base64 encoded or plain string
	ContentType        string
	Deleted            bool
	OwnerAddress       string
	StringAnnotations  map[string]string
	NumericAnnotations map[string]interface{} // Can be number or string for range queries
}

// EntityUpdateRequest represents a request to update an existing entity.
// It is expected to carry all entity data needed to build an OPUpdate.
// The entity key is taken from the URL path (`/entities/{key}`) and will override
// any `key` value provided in JSON.
type EntityUpdateRequest struct {
	Key                string                 `json:"key,omitempty"`
	ExpiresIn          int64                  `json:"expiresIn"`
	Payload            string                 `json:"payload,omitempty"` // base64 encoded or plain string
	ContentType        string                 `json:"contentType"`
	Deleted            bool                   `json:"deleted,omitempty"`
	OwnerAddress       string                 `json:"ownerAddress"`
	StringAnnotations  map[string]string      `json:"stringAnnotations,omitempty"`
	NumericAnnotations map[string]interface{} `json:"numericAnnotations,omitempty"`
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
