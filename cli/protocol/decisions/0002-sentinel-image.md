# Decision 0002: sentinel image is test-only

Status: accepted

Phase 0 through transport development use a deterministic sentinel Skill
Runtime Image whose manifest exercises ordered prompt placement, byte hashing,
task separation, and the terminal-envelope carriage path. Its manifest is
marked `releaseEligible: false`.

Build and release admission must fail if that marker is false or if the
canonical language-owned image, task schema, terminal schema, and named
semantic profile are absent. Adapter code consumes the image through the
shared opaque interface and may not branch on its Markdown body.
