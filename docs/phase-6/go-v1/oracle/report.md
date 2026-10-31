# gin go/types oracle (0f09c3a9b462, GOOS=linux GOARCH=amd64), rescored
Lookup: last selector identifier of qualified evidence; receiver type identifier for receiver references.
Resolved Go edges: agree=3019, disagree=0, no-oracle-use=63. Precision 1.0000.
No-oracle-use: file outside the default linux/amd64 package build (build tags, testdata)=63
Ambiguous edges whose oracle target is a candidate: 52.
Unresolved calls whose oracle target is a gin symbol: 1618 — method calls on anything but the receiver, promoted methods, interface dispatch=1568; calls of function values (package-level variables)=50

## Disagreements (0; suspect lookups: 0)

## Unresolved call gap sample
- method calls on anything but the receiver, promoted methods, interface dispatch: `pairs.searchCredential` (auth.go) → symbol:auth.go#authPairs.searchCredential, `c.requestHeader` (auth.go) → symbol:context.go#Context.requestHeader, `c.Header` (auth.go) → symbol:context.go#Context.Header, `c.AbortWithStatus` (auth.go) → symbol:context.go#Context.AbortWithStatus, `c.Set` (auth.go) → symbol:context.go#Context.Set, `pairs.searchCredential` (auth.go) → symbol:auth.go#authPairs.searchCredential, `c.requestHeader` (auth.go) → symbol:context.go#Context.requestHeader
- calls of function values (package-level variables): `DebugPrintRouteFunc` (debug.go) → symbol:debug.go#DebugPrintRouteFunc, `DebugPrintFunc` (debug.go) → symbol:debug.go#DebugPrintFunc, `engine` (ginS/gins.go) → symbol:ginS/gins.go#engine, `engine` (ginS/gins.go) → symbol:ginS/gins.go#engine, `engine` (ginS/gins.go) → symbol:ginS/gins.go#engine, `engine` (ginS/gins.go) → symbol:ginS/gins.go#engine, `engine` (ginS/gins.go) → symbol:ginS/gins.go#engine
