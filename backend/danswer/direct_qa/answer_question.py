import time

from danswer.chunking.models import InferenceChunk
from danswer.configs.app_configs import NUM_GENERATIVE_AI_INPUT_DOCS
from danswer.configs.app_configs import QA_TIMEOUT
from danswer.datastores.qdrant.store import QdrantIndex
from danswer.datastores.typesense.store import TypesenseIndex
from danswer.db.models import User
from danswer.direct_qa import get_default_backend_qa_model
from danswer.search.danswer_helper import query_intent
from danswer.search.keyword_search import retrieve_keyword_documents
from danswer.search.models import SearchType
from danswer.search.semantic_search import chunks_to_search_docs
from danswer.search.semantic_search import retrieve_ranked_documents
from danswer.server.models import QAResponse
from danswer.server.models import QuestionRequest
from danswer.utils.logging import setup_logger

logger = setup_logger()


def answer_question(question: QuestionRequest, user: User | None) -> QAResponse:
    start_time = time.time()

    query = question.query
    collection = question.collection
    filters = question.filters
    use_keyword = question.use_keyword
    offset_count = question.offset if question.offset is not None else 0
    logger.info(f"Received QA query: {query}")

    predicted_search, predicted_flow = query_intent(query)
    if use_keyword is None:
        use_keyword = predicted_search == SearchType.KEYWORD

    user_id = None if user is None else user.id
    if use_keyword:
        ranked_chunks: list[InferenceChunk] | None = retrieve_keyword_documents(
            query, user_id, filters, TypesenseIndex(collection)
        )
        unranked_chunks: list[InferenceChunk] | None = []
    else:
        ranked_chunks, unranked_chunks = retrieve_ranked_documents(
            query, user_id, filters, QdrantIndex(collection)
        )
    if not ranked_chunks:
        return QAResponse(
            answer=None,
            quotes=None,
            top_ranked_docs=None,
            lower_ranked_docs=None,
            predicted_flow=predicted_flow,
            predicted_search=predicted_search,
        )

    qa_model = get_default_backend_qa_model(timeout=QA_TIMEOUT)
    chunk_offset = offset_count * NUM_GENERATIVE_AI_INPUT_DOCS
    if chunk_offset >= len(ranked_chunks):
        raise ValueError("Chunks offset too large, should not retry this many times")

    error_msg = None
    try:
        answer, quotes = qa_model.answer_question(
            query,
            ranked_chunks[chunk_offset : chunk_offset + NUM_GENERATIVE_AI_INPUT_DOCS],
        )
    except Exception as e:
        # exception is logged in the answer_question method, no need to re-log
        answer, quotes = None, None
        error_msg = f"Error occurred in call to LLM - {e}"

    logger.info(f"Total QA took {time.time() - start_time} seconds")

    return QAResponse(
        answer=answer,
        quotes=quotes,
        top_ranked_docs=chunks_to_search_docs(ranked_chunks),
        lower_ranked_docs=chunks_to_search_docs(unranked_chunks),
        predicted_flow=predicted_flow,
        predicted_search=predicted_search,
        error_msg=error_msg,
    )
