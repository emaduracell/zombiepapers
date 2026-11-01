-- 004_api_cache: responses from external APIs (OpenAlex), so repeated runs cost nothing.
--
-- The URL never contains the API key: the key is added only when the request is sent.
-- Entries do not expire yet; fetched_at lets us add a refresh rule later.

CREATE TABLE api_cache (
    url        VARCHAR PRIMARY KEY,  -- request URL without the API key, e.g.
                                     -- 'https://api.openalex.org/works/doi:10.1000/abc'
    fetched_at TIMESTAMP NOT NULL,
    json       VARCHAR NOT NULL      -- response body as JSON text; 'null' = looked up, not found
);
