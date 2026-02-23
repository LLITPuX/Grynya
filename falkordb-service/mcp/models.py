from typing import Optional, Literal, Dict, Any, List
from pydantic import BaseModel, Field

NodeType = Literal["Session", "Request", "Response", "Feedback", "Analysis", "Entity", "Year", "Day"]
RelationType = Literal["PART_OF", "RESPONDS_TO", "FEEDBACK_ON", "ANALYZES", "SUMMARIZES", "NEXT", "LAST_EVENT", "MONTH", "HAPPENED_AT", "INVOLVES", "MENTIONS"]

class NodeProps(BaseModel):
    id: str
    name: Optional[str] = None
    
class RelationProps(BaseModel):
    pass # Base for relation properties

class SessionNode(NodeProps):
    topic: str
    status: Literal["active", "closed"]
    trigger: Literal["/db", "/sa", "/ss"]

class RequestNode(NodeProps):
    author: Literal["user"] = "user"
    text: str
    type: Literal["text", "command"] = "text"

class ResponseNode(NodeProps):
    author: Literal["Grynya"] = "Grynya"
    summary: str
    full_text: str
    type: Literal["text"] = "text"

class FeedbackNode(NodeProps):
    author: Literal["user"] = "user"
    text: str

class AnalysisNode(NodeProps):
    type: Literal["response_analysis", "session_summary"]
    verdict: Literal["correct", "partially_correct", "incorrect"]
    rules_used: str
    rules_ignored: str
    errors: str
    lessons: str

class EntityNode(NodeProps):
    type: Literal["Technology", "Concept", "Project", "Person", "Rule"]
    description: str

class YearNode(NodeProps):
    value: int

class DayNode(NodeProps):
    date: str

class LinkDefinition(BaseModel):
    source_id: str
    target_id: str
    rel_type: RelationType
    props: Optional[Dict[str, Any]] = None

# MCP Tool Arguments
class CreateSessionArgs(BaseModel):
    session_id: str
    topic: str
    trigger: Literal["/db", "/sa", "/ss"]
    last_event_id: str
    time: str # HH:MM:SS
    date: str # YYYY-MM-DD
    year: int # YYYY

class AddNodeArgs(BaseModel):
    node_type: NodeType
    node_data: Dict[str, Any] # Must match corresponding node model
    day_id: str
    time: Optional[str] = None # Time is optional for entities

class RelLink(BaseModel):
    rel_type: RelationType
    target_id: str
    props: Optional[Dict[str, Any]] = None

class AddComplexNodeArgs(BaseModel):
    """
    Combines adding a node and its initial set of relations.
    Equivalent to how memory_bridge handled adding a node with 'relations' array.
    """
    node_type: NodeType
    node_data: Dict[str, Any]
    day_id: Optional[str] = None
    time: Optional[str] = None
    relations: List[RelLink] = Field(default_factory=list)

class QueryGraphArgs(BaseModel):
    query: str
    
class UpdateLastEventArgs(BaseModel):
    session_id: str
    event_id: str

class LinkNodesArgs(BaseModel):
    source_id: str
    target_id: str
    rel_type: RelationType
    props: Optional[Dict[str, Any]] = None
