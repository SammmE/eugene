import uuid
import datetime

code = open('src/eugene/services.py', 'r', encoding='utf-8').read()

to_remove = """        if self._collection is not None and self._embedder is not None:
            self._collection.add(
                ids=[str(uuid4())],
                documents=[text],
                embeddings=[self._embed(text)],
                metadatas=[{"session_id": session_id, "created_at": datetime.utcnow().isoformat()}],
            )
"""

code = code.replace(to_remove, "")

insert_target = """    async def consolidate_exchange(self, session_id: str, user_text: str, assistant_text: str) -> None:
        combined = f"User: {user_text}\\nAssistant: {assistant_text}"
        await self.store_exchange(session_id, combined)"""

new_pipeline = """    async def consolidate_exchange(self, session_id: str, user_text: str, assistant_text: str) -> None:
        combined = f"User: {user_text}\\nAssistant: {assistant_text}"
        await self.store_exchange(session_id, combined)
        import asyncio
        asyncio.create_task(self._extract_and_mutate_facts(session_id, user_text, assistant_text))

    async def _extract_and_mutate_facts(self, session_id: str, user_text: str, assistant_text: str) -> None:
        if self._collection is None or self._embedder is None:
            return
        
        prompt = (
            "You are a background fact extractor. Extract any new, distinct factual assertions "
            "made by the user or the assistant in the following exchange. "
            "For each fact, generate a short, general search query that could be used to find existing related facts "
            "in a vector database.\\n\\n"
            f"User: {user_text}\\nAssistant: {assistant_text}\\n\\n"
            "Respond ONLY with a JSON object in this format:\\n"
            "{\\\"facts\\\": [{\\\"fact\\\": \\\"string\\\", \\\"search_query\\\": \\\"string\\\"}]}"
        )
        
        try:
            extraction_result = await self.services.provider.complete(
                model=self.services.config.default_model,
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                origin="memory_extraction"
            )
            
            import json
            text_result = extraction_result.text
            start = text_result.find('{')
            end = text_result.rfind('}')
            if start == -1 or end == -1:
                return
            data = json.loads(text_result[start:end+1])
            facts = data.get("facts", [])
            if not facts:
                return
        except Exception as e:
            from loguru import logger
            logger.bind(component="memory").error("Fact extraction failed error={error}", error=str(e))
            return

        existing_facts_context = []
        for item in facts:
            query = item.get("search_query")
            if not query: 
                continue
            
            embedding = self._embed(query)
            result = self._collection.query(query_embeddings=[embedding], n_results=3)
            docs = result.get("documents", [[]])[0]
            ids = result.get("ids", [[]])[0]
            
            for doc_id, doc in zip(ids, docs):
                existing_facts_context.append({"id": doc_id, "fact": doc, "related_to": item.get("fact")})
        
        unique_existing = {f["id"]: f["fact"] for f in existing_facts_context}
        
        decision_prompt = (
            "You are a memory consolidation manager. "
            "Based on the following NEW extracted facts and EXISTING facts from the database, "
            "determine what operations to run to keep the database accurate and deduplicated. "
            "If a NEW fact contradicts or updates an EXISTING fact, UPSERT the existing ID with the new fact, or DELETE the existing ID and UPSERT a new ID.\\n\\n"
            "NEW FACTS:\\n"
            f"{json.dumps(facts, indent=2)}\\n\\n"
            "EXISTING FACTS:\\n"
            f"{json.dumps(unique_existing, indent=2)}\\n\\n"
            "Operations available:\\n"
            "- UPSERT: Update an existing fact by its ID, or insert a new fact (use a new unique ID if creating new).\\n"
            "- DELETE: Remove a fact by its ID (if it's contradicted or completely superseded).\\n\\n"
            "Respond ONLY with a JSON object in this format:\\n"
            "{\\\"operations\\\": [{\\\"action\\\": \\\"UPSERT\\\" or \\\"DELETE\\\", \\\"id\\\": \\\"string\\\", \\\"fact\\\": \\\"string (only for UPSERT)\\\"}]}"
        )
        
        try:
            decision_result = await self.services.provider.complete(
                model=self.services.config.default_model,
                messages=[{"role": "user", "content": decision_prompt}],
                tools=[],
                origin="memory_decision"
            )
            
            text_result = decision_result.text
            start = text_result.find('{')
            end = text_result.rfind('}')
            if start == -1 or end == -1:
                return
            decision_data = json.loads(text_result[start:end+1])
            ops = decision_data.get("operations", [])
        except Exception as e:
            from loguru import logger
            logger.bind(component="memory").error("Memory decision failed error={error}", error=str(e))
            return
            
        for op in ops:
            action = op.get("action")
            op_id = op.get("id")
            fact = op.get("fact")
            
            from uuid import uuid4
            from datetime import datetime
            if not op_id:
                op_id = str(uuid4())
                
            try:
                if action == "UPSERT" and fact:
                    embedding = self._embed(fact)
                    self._collection.upsert(
                        ids=[op_id],
                        documents=[fact],
                        embeddings=[embedding],
                        metadatas=[{"session_id": session_id, "updated_at": datetime.utcnow().isoformat()}]
                    )
                    from loguru import logger
                    logger.bind(component="memory").info("Memory UPSERT executed id={id} fact={fact}", id=op_id, fact=fact)
                elif action == "DELETE" and op_id:
                    self._collection.delete(ids=[op_id])
                    from loguru import logger
                    logger.bind(component="memory").info("Memory DELETE executed id={id}", id=op_id)
            except Exception as e:
                from loguru import logger
                logger.bind(component="memory").error("Chroma operation failed error={error}", error=str(e))"""

code = code.replace(insert_target, new_pipeline)
open('src/eugene/services.py', 'w', encoding='utf-8').write(code)
print("done")
