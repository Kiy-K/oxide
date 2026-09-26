use super::NativeModelSpec;

pub(super) fn native_model_spec(profile: &str) -> anyhow::Result<NativeModelSpec> {
    use fastembed::EmbeddingModel::*;
    Ok(match profile {
        "embeddinggemma-300m" => NativeModelSpec {
            model: EmbeddingGemma300M,
            model_id: "embeddinggemma-300m",
            quantization: "fp32",
            query_prefix: "",
            document_prefix: "title: none | text: ",
            pooling: "graph-baked",
        },
        "embeddinggemma-300m-q4" => NativeModelSpec {
            model: EmbeddingGemma300MQ4,
            model_id: "embeddinggemma-300m",
            quantization: "int4",
            query_prefix: "",
            document_prefix: "title: none | text: ",
            pooling: "graph-baked",
        },
        // BAAI/bge-small-en-v1.5 model card: query-only instruction prefix,
        // no document-side prefix ("no instruction needs to be added to
        // passages").
        "bge-small-en-v1.5" => NativeModelSpec {
            model: BGESmallENV15,
            model_id: "bge-small-en-v1.5",
            quantization: "fp32",
            query_prefix: "Represent this sentence for searching relevant passages: ",
            document_prefix: "",
            pooling: "cls",
        },
        // Quantized (int8) BGESmallENV15 — same model card/prompt convention
        // as fp32, different onnx weights (Qdrant/bge-small-en-v1.5-onnx-Q).
        // Added for the CPU-first tiny-embedder screen (see
        // docs/cpu-embedding-survey/quantized-tiny-screen.md); quantization
        // does not change the documented prompt or pooling convention.
        "bge-small-en-v1.5-q" => NativeModelSpec {
            model: BGESmallENV15Q,
            model_id: "bge-small-en-v1.5",
            quantization: "int8",
            query_prefix: "Represent this sentence for searching relevant passages: ",
            document_prefix: "",
            pooling: "cls",
        },
        // snowflake/snowflake-arctic-embed-{xs,s} model cards: same query
        // prefix convention as BGE, CLS pooling (confirmed against fastembed's
        // own `get_default_pooling_method` table, which matches).
        "arctic-embed-xs" => NativeModelSpec {
            model: SnowflakeArcticEmbedXS,
            model_id: "snowflake-arctic-embed-xs",
            quantization: "fp32",
            query_prefix: "Represent this sentence for searching relevant passages: ",
            document_prefix: "",
            pooling: "cls",
        },
        // Quantized (int8) SnowflakeArcticEmbedXS — same repo, quantized onnx.
        "arctic-embed-xs-q" => NativeModelSpec {
            model: SnowflakeArcticEmbedXSQ,
            model_id: "snowflake-arctic-embed-xs",
            quantization: "int8",
            query_prefix: "Represent this sentence for searching relevant passages: ",
            document_prefix: "",
            pooling: "cls",
        },
        "arctic-embed-s" => NativeModelSpec {
            model: SnowflakeArcticEmbedS,
            model_id: "snowflake-arctic-embed-s",
            quantization: "fp32",
            query_prefix: "Represent this sentence for searching relevant passages: ",
            document_prefix: "",
            pooling: "cls",
        },
        // jinaai/jina-embeddings-v2-base-code model card: plain mean-pooled
        // bi-encoder, no query/document instruction convention (predates
        // Jina v3's task-instruction prompts).
        "jina-code-v2" => NativeModelSpec {
            model: JinaEmbeddingsV2BaseCode,
            model_id: "jina-embeddings-v2-base-code",
            quantization: "fp32",
            query_prefix: "",
            document_prefix: "",
            pooling: "mean",
        },
        // sentence-transformers/all-MiniLM-L6-v2: plain baseline, no prefix.
        "minilm-l6-v2" => NativeModelSpec {
            model: AllMiniLML6V2,
            model_id: "all-MiniLM-L6-v2",
            quantization: "fp32",
            query_prefix: "",
            document_prefix: "",
            pooling: "mean",
        },
        // Quantized (int8) AllMiniLML6V2 — same repo, quantized onnx.
        "minilm-l6-v2-q" => NativeModelSpec {
            model: AllMiniLML6V2Q,
            model_id: "all-MiniLM-L6-v2",
            quantization: "int8",
            query_prefix: "",
            document_prefix: "",
            pooling: "mean",
        },
        other => anyhow::bail!(
            "unsupported native embedding profile {other:?}; supported: \
             embeddinggemma-300m, embeddinggemma-300m-q4, bge-small-en-v1.5, \
             bge-small-en-v1.5-q, arctic-embed-xs, arctic-embed-xs-q, \
             arctic-embed-s, jina-code-v2, minilm-l6-v2, minilm-l6-v2-q"
        ),
    })
}
