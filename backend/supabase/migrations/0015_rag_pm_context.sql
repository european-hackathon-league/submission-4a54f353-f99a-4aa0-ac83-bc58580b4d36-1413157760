-- The RAG index also holds what the PMs told the system: series briefs, stances,
-- open questions and their answers, meeting notes, feedback on requirements and
-- comments. The sidebar agent reads them through its own tool (search_context)
-- and labels them as PM context, never as customer evidence.

select drop_checks_like('rag_chunk', 'source_record');
alter table rag_chunk add constraint rag_chunk_object_type_check check (object_type in (
    'case', 'requirement', 'signal', 'source_record', 'decision', 'audit_event',
    'series_brief', 'case_stance', 'open_question', 'meeting_note', 'requirement_feedback', 'comment'));
