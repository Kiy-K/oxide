# Spot check of resolved Java edges (measured; manual reading against the checkouts)

Samples: the 10 resolved cross-file calls and 10 resolved imports in each `*-audit.json`. For each one I read the
expression's declaration context (local/field type, import, static import) and the target declaration in the
pinned checkout. A row is correct when javac would bind the expression to that declaration.

## gson

| Kind | Expression | Target | Correct | Basis |
| --- | --- | --- | --- | --- |
| calls | `writer.beginObject` | `gson/src/main/java/com/google/gson/internal/bind/JsonTreeWriter.java#JsonTreeWriter.beginObject` | yes | `JsonTreeWriter writer` local; override declared in JsonTreeWriter |
| calls | `jsonWriter.endArray` | `gson/src/main/java/com/google/gson/stream/JsonWriter.java#JsonWriter.endArray` | yes | `JsonWriter jsonWriter` local |
| calls | `new JsonReader` | `gson/src/main/java/com/google/gson/stream/JsonReader.java#JsonReader` | yes | constructor of the imported/same-package class |
| calls | `new JsonReader` | `gson/src/main/java/com/google/gson/stream/JsonReader.java#JsonReader` | yes | constructor of the imported/same-package class |
| calls | `reader.beginObject` | `gson/src/main/java/com/google/gson/stream/JsonReader.java#JsonReader.beginObject` | yes | `JsonReader reader` local inside the for loop |
| calls | `new JsonArray` | `gson/src/main/java/com/google/gson/JsonArray.java#JsonArray` | yes | same-package class |
| calls | `writer.close` | `gson/src/main/java/com/google/gson/stream/JsonWriter.java#JsonWriter.close` | yes | `JsonWriter writer` local |
| calls | `new JsonPrimitive` | `gson/src/main/java/com/google/gson/JsonPrimitive.java#JsonPrimitive` | yes | same-package class |
| calls | `new Gson` | `gson/src/main/java/com/google/gson/Gson.java#Gson` | yes | same-package class |
| calls | `new Gson` | `gson/src/main/java/com/google/gson/Gson.java#Gson` | yes | same-package class |
| imports | `import com.google.gson.common.TestTypes.BagOfPrimitives;` | `gson/src/test/java/com/google/gson/common/TestTypes.java#TestTypes.BagOfPrimitives` | yes | fully qualified name matches the target's package path and type |
| imports | `import com.google.gson.Gson;` | `gson/src/main/java/com/google/gson/Gson.java#Gson` | yes | fully qualified name matches the target's package path and type |
| imports | `import com.google.gson.Gson;` | `gson/src/main/java/com/google/gson/Gson.java#Gson` | yes | fully qualified name matches the target's package path and type |
| imports | `import com.google.gson.stream.JsonToken;` | `gson/src/main/java/com/google/gson/stream/JsonToken.java#JsonToken` | yes | fully qualified name matches the target's package path and type |
| imports | `import com.google.gson.stream.JsonReader;` | `gson/src/main/java/com/google/gson/stream/JsonReader.java#JsonReader` | yes | fully qualified name matches the target's package path and type |
| imports | `import com.google.gson.JsonDeserializationContext;` | `gson/src/main/java/com/google/gson/JsonDeserializationContext.java#JsonDeserializationContext` | yes | fully qualified name matches the target's package path and type |
| imports | `import com.google.gson.reflect.TypeToken;` | `gson/src/main/java/com/google/gson/reflect/TypeToken.java#TypeToken` | yes | fully qualified name matches the target's package path and type |
| imports | `import com.google.gson.reflect.TypeToken;` | `gson/src/main/java/com/google/gson/reflect/TypeToken.java#TypeToken` | yes | fully qualified name matches the target's package path and type |
| imports | `import com.google.gson.JsonElement;` | `gson/src/main/java/com/google/gson/JsonElement.java#JsonElement` | yes | fully qualified name matches the target's package path and type |
| imports | `import com.google.gson.JsonArray;` | `gson/src/main/java/com/google/gson/JsonArray.java#JsonArray` | yes | fully qualified name matches the target's package path and type |

## mockito

| Kind | Expression | Target | Correct | Basis |
| --- | --- | --- | --- | --- |
| calls | `times` | `mockito-core/src/main/java/org/mockito/Mockito.java#Mockito.times` | yes | `import static org.mockito.BDDMockito.times`; BDDMockito extends Mockito, and javac binds the inherited static Mockito.times |
| calls | `async.runAfter` | `mockito-core/src/testFixtures/java/org/mockitoutil/async/AsyncTesting.java#AsyncTesting.runAfter` | yes | field `AsyncTesting async`, imported from org.mockitoutil.async |
| calls | `calls` | `mockito-core/src/main/java/org/mockito/Mockito.java#Mockito.calls` | yes | `import static org.mockito.Mockito.calls` |
| calls | `moarMethods.simpleMethod` | `mockito-core/src/testFixtures/java/org/mockitousage/IMethods.java#IMethods.simpleMethod` | yes | field `@Mock IMethods moarMethods` |
| calls | `when` | `mockito-core/src/main/java/org/mockito/Mockito.java#Mockito.when` | yes | static import of Mockito.when (single or on-demand `Mockito.*`) |
| calls | `captor.getValue` | `mockito-core/src/main/java/org/mockito/ArgumentCaptor.java#ArgumentCaptor.getValue` | yes | local/field `ArgumentCaptor<Person> captor` |
| calls | `new SerializableMethod` | `mockito-core/src/main/java/org/mockito/internal/invocation/SerializableMethod.java#SerializableMethod` | yes | imported class |
| calls | `inferFrom` | `mockito-core/src/main/java/org/mockito/internal/util/reflection/GenericMetadataSupport.java#GenericMetadataSupport.inferFrom` | yes | `import static ...GenericMetadataSupport.inferFrom` |
| calls | `VerificationModeFactory.atLeastOnce` | `mockito-core/src/main/java/org/mockito/internal/verification/VerificationModeFactory.java#VerificationModeFactory.atLeastOnce` | yes | imported type, static method |
| calls | `when` | `mockito-core/src/main/java/org/mockito/Mockito.java#Mockito.when` | yes | static import of Mockito.when (single or on-demand `Mockito.*`) |
| imports | `import org.mockito.InOrder;` | `mockito-core/src/main/java/org/mockito/InOrder.java#InOrder` | yes | fully qualified name matches the target's package path and type |
| imports | `import static org.mockito.internal.exceptions.Reporter.notAMockPassedToVerify;` | `mockito-core/src/main/java/org/mockito/internal/exceptions/Reporter.java#Reporter.notAMockPassedToVerify` | yes | fully qualified name matches the target's package path and type |
| imports | `import org.mockitoutil.TestBase;` | `mockito-core/src/testFixtures/java/org/mockitoutil/TestBase.java#TestBase` | yes | fully qualified name matches the target's package path and type |
| imports | `import org.mockito.InjectMocks;` | `mockito-core/src/main/java/org/mockito/InjectMocks.java#InjectMocks` | yes | fully qualified name matches the target's package path and type |
| imports | `import org.mockito.internal.junit.UniversalTestListener;` | `mockito-core/src/main/java/org/mockito/internal/junit/UniversalTestListener.java#UniversalTestListener` | yes | fully qualified name matches the target's package path and type |
| imports | `import org.mockitoutil.TestBase;` | `mockito-core/src/testFixtures/java/org/mockitoutil/TestBase.java#TestBase` | yes | fully qualified name matches the target's package path and type |
| imports | `import org.mockito.stubbing.Answer;` | `mockito-core/src/main/java/org/mockito/stubbing/Answer.java#Answer` | yes | fully qualified name matches the target's package path and type |
| imports | `import org.mockito.internal.verification.api.VerificationData;` | `mockito-core/src/main/java/org/mockito/internal/verification/api/VerificationData.java#VerificationData` | yes | fully qualified name matches the target's package path and type |
| imports | `import org.mockito.Mock;` | `mockito-core/src/main/java/org/mockito/Mock.java#Mock` | yes | fully qualified name matches the target's package path and type |
| imports | `import org.mockitousage.IMethods;` | `mockito-core/src/testFixtures/java/org/mockitousage/IMethods.java#IMethods` | yes | fully qualified name matches the target's package path and type |

## Counts

gson: 20/20 correct; mockito: 20/20 correct. No contradictions. The samples are mostly simple local-typed receivers, static imports and constructors, so this check
bounds gross errors only; [../oracle/report.md](../oracle/report.md) has the javac-attributed precision.
