# Java edge spot-check

Pinned checkout HEADs: gson `854c8255b625cf1e13c701a83ea9ccb4caaa576a`; mockito `116a4d6231dc35f90543060af41dcde00fe4e2ae`.

I inspected each source use and the target declaration/imported type in the corresponding checkout. All sampled edges point to the declaration/type named by Java source; no contradictions found.

| Repository | Kind | # | Expression → target | Correct | Why |
|---|---|---:|---|---|---|
| gson | call | 1 | `writer.beginObject` → `JsonTreeWriter.beginObject` | yes | Receiver is the local tree writer. |
| gson | call | 2 | `jsonWriter.endArray` → `JsonWriter.endArray` | yes | Receiver is a JsonWriter. |
| gson | call | 3 | `new JsonReader` → `JsonReader` | yes | Constructor's enclosing type. |
| gson | call | 4 | `new JsonReader` → `JsonReader` | yes | Constructor's enclosing type across metrics module. |
| gson | call | 5 | `reader.beginObject` → `JsonReader.beginObject` | yes | Receiver is a JsonReader. |
| gson | call | 6 | `new JsonArray` → `JsonArray` | yes | Constructor's enclosing type. |
| gson | call | 7 | `writer.close` → `JsonWriter.close` | yes | Receiver is a JsonWriter. |
| gson | call | 8 | `new JsonPrimitive` → `JsonPrimitive` | yes | Constructor's enclosing type. |
| gson | call | 9 | `new Gson` → `Gson` | yes | Constructor's enclosing type. |
| gson | call | 10 | `new Gson` → `Gson` | yes | Constructor's enclosing type. |
| gson | import | 1 | `com.google.gson.common.TestTypes.BagOfPrimitives` → nested `BagOfPrimitives` | yes | Import names the declared nested class. |
| gson | import | 2 | `com.google.gson.Gson` → `Gson` | yes | Imported top-level type. |
| gson | import | 3 | `com.google.gson.Gson` → `Gson` | yes | Imported top-level type. |
| gson | import | 4 | `com.google.gson.stream.JsonToken` → `JsonToken` | yes | Imported enum. |
| gson | import | 5 | `com.google.gson.stream.JsonReader` → `JsonReader` | yes | Imported top-level type. |
| gson | import | 6 | `com.google.gson.JsonDeserializationContext` → interface | yes | Imported top-level type. |
| gson | import | 7 | `com.google.gson.reflect.TypeToken` → `TypeToken` | yes | Imported generic class. |
| gson | import | 8 | `com.google.gson.reflect.TypeToken` → `TypeToken` | yes | Imported generic class. |
| gson | import | 9 | `com.google.gson.JsonElement` → `JsonElement` | yes | Imported top-level type. |
| gson | import | 10 | `com.google.gson.JsonArray` → `JsonArray` | yes | Imported top-level type. |
| mockito | call | 1 | `times(1)` → `Mockito.times` | yes | Static import resolves to Mockito's method. |
| mockito | call | 2 | `async.runAfter` → `AsyncTesting.runAfter` | yes | Receiver's fixture type declares method. |
| mockito | call | 3 | `calls(1)` → `Mockito.calls` | yes | Static import resolves to Mockito's method. |
| mockito | call | 4 | `moarMethods.simpleMethod` → `IMethods.simpleMethod` | yes | Receiver implements IMethods. |
| mockito | call | 5 | `when(...)` → `Mockito.when` | yes | Static import resolves to Mockito's method. |
| mockito | call | 6 | `captor.getValue` → `ArgumentCaptor.getValue` | yes | Receiver is an ArgumentCaptor. |
| mockito | call | 7 | `new SerializableMethod` → `SerializableMethod` | yes | Constructor's enclosing type. |
| mockito | call | 8 | `inferFrom` → `GenericMetadataSupport.inferFrom` | yes | Static import resolves to method. |
| mockito | call | 9 | `VerificationModeFactory.atLeastOnce` → `VerificationModeFactory.atLeastOnce` | yes | Qualified static invocation names target owner/method. |
| mockito | call | 10 | `when(...)` → `Mockito.when` | yes | Static import resolves to Mockito's method. |
| mockito | import | 1 | `org.mockito.InOrder` → `InOrder` | yes | Imported interface. |
| mockito | import | 2 | static `Reporter.notAMockPassedToVerify` → method | yes | Static import names declared method. |
| mockito | import | 3 | `org.mockitoutil.TestBase` → `TestBase` | yes | Imported fixture class. |
| mockito | import | 4 | `org.mockito.InjectMocks` → annotation | yes | Imported annotation type. |
| mockito | import | 5 | `UniversalTestListener` → type | yes | Imported class is the field's declared type. |
| mockito | import | 6 | `org.mockitoutil.TestBase` → `TestBase` | yes | Imported fixture class. |
| mockito | import | 7 | `org.mockito.stubbing.Answer` → interface | yes | Imported interface used as parameter type. |
| mockito | import | 8 | `VerificationData` → interface | yes | Imported interface used as parameter type. |
| mockito | import | 9 | `org.mockito.Mock` → annotation | yes | Imported annotation type. |
| mockito | import | 10 | `org.mockitousage.IMethods` → interface | yes | Imported fixture interface. |

Counts: gson 10/10 calls and 10/10 imports correct; mockito 10/10 calls and 10/10 imports correct. Contradictions: 0.
