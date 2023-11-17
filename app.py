import os
import hmac
import streamlit as st
import requests

os.environ["OPENAI_API_KEY"] = st.secrets['OPENAI_API_KEY']
#os.environ["LANGCHAIN_API_KEY"] = st.secrets['LANGCHAIN_API_KEY']
#os.environ["LANGCHAIN_TRACING_V2"] = "true"
#os.environ["LANGCHAIN_ENDPOINT"] = st.secrets['LANGCHAIN_ENDPOINT']
#os.environ["LANGCHAIN_PROJECT"] = st.secrets['LANGCHAIN_PROJECT']

import pandas as pd

from cassandra.cluster import Cluster
from cassandra.auth import PlainTextAuthProvider

from langchain.chat_models import ChatOpenAI
from langchain.vectorstores import Cassandra
from langchain.embeddings import OpenAIEmbeddings
from langchain.memory import ConversationBufferWindowMemory
from langchain.memory import CassandraChatMessageHistory

import tempfile
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.document_loaders import PyPDFLoader

from langchain.schema import HumanMessage, AIMessage
from langchain.prompts import ChatPromptTemplate
from langchain.schema.runnable import RunnableMap

from langchain.callbacks.base import BaseCallbackHandler

print("Started")


# Streaming call back handler for responses
class StreamHandler(BaseCallbackHandler):
    def __init__(self, container, initial_text=""):
        self.container = container
        self.text = initial_text

    def on_llm_new_token(self, token: str, **kwargs):
        self.text += token
        self.container.markdown(self.text + "▌")


#################
### Constants ###
#################

# Define the number of docs to retrieve from the vectorstore and memory
top_k_vectorstore = 4
top_k_memory = 3

# Define the language option for localization
language = 'ro_RO'

# Defines the vector tables, memory and rails to use
username = st.secrets["USERNAME"]

###############
### Globals ###
###############

global lang_dict
global rails_dict
global session
global embedding
global vectorstore
global retriever
global model
global chat_history
global memory


#################
### Functions ###
#################

# Close off the app using a password
def check_password():
    """Returns `True` if the user had the correct password."""

    def password_entered():
        """Checks whether a password entered by the user is correct."""
        if hmac.compare_digest(st.session_state["password"], st.secrets["PASSWORD"]):
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # Don't store the password.
        else:
            st.session_state["password_correct"] = False

    # Return True if the password is validated.
    if st.session_state.get("password_correct", False):
        return True

    # Show input for password.
    st.text_input(
        lang_dict['password'], type="password", on_change=password_entered, key="password"
    )
    if "password_correct" in st.session_state:
        st.error(lang_dict['password_incorrect'])
    return False


# Function for Vectorizing uploaded data into Astra DB
def vectorize_text(uploaded_files):
    for uploaded_file in uploaded_files:
        if uploaded_file is not None:

            # Write to temporary file
            temp_dir = tempfile.TemporaryDirectory()
            file = uploaded_file
            print(f"""Processing: {file}""")
            temp_filepath = os.path.join(temp_dir.name, file.name)
            with open(temp_filepath, "wb") as f:
                f.write(file.getvalue())

            # Create the text splitter
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=1500,
                chunk_overlap=100
            )

            if uploaded_file.name.endswith('txt'):
                file = [uploaded_file.read().decode()]
                texts = text_splitter.create_documents(file, [{'source': uploaded_file.name}])
                vectorstore.add_documents(texts)
                st.info(f"{len(texts)} {lang_dict['load_text']}")

            if uploaded_file.name.endswith('pdf'):
                # Read PDF
                docs = []
                loader = PyPDFLoader(temp_filepath)
                docs.extend(loader.load())

                pages = text_splitter.split_documents(docs)
                vectorstore.add_documents(pages)
                st.info(f"{len(pages)} {lang_dict['load_pdf']}")


################################
### Resources and Data Cache ###
################################

# Cache localized strings
@st.cache_data()
def load_localization(locale):
    print("load_localization")
    # Load in the text bundle and filter by language locale
    df = pd.read_csv("localization.csv")
    df = df.query(f"locale == '{locale}'")
    # Create and return a dictionary of key/values.
    lang_dict = {df.key.to_list()[i]: df.value.to_list()[i] for i in range(len(df.key.to_list()))}
    return lang_dict


lang_dict = load_localization(language)


# Cache localized strings
@st.cache_data()
def load_rails(username):
    print("load_rails")
    # Load in the rails bundle and filter by username
    df = pd.read_csv("rails.csv")
    df = df.query(f"username == '{username}'")
    # Create and return a dictionary of key/values.
    rails_dict = {df.key.to_list()[i]: df.value.to_list()[i] for i in range(len(df.key.to_list()))}
    return rails_dict


# Cache Astra DB session for future runs
def download_file(url, file_name):
    # Send a GET request to the URL
    response = requests.get(url)

    # Check if the request was successful
    if response.status_code == 200:
        # Get the full path of the directory where the file will be saved
        dir_path = os.getcwd()
        full_path = os.path.join(dir_path, file_name)

        # Open a file with the specified file name
        # 'wb' mode is used to write the file in binary mode
        with open(full_path, 'wb') as file:
            file.write(response.content)
        return full_path
    else:
        print(f"Failed to download the file. Status code: {response.status_code}")
        return None
# Cache Astra DB session for future runs
@st.cache_resource(show_spinner=lang_dict['connect_astra'])
def load_session():
    print("load_session")
    bundle_url = st.secrets["ASTRA_SCB_PATH"]  # Replace with your URL
    bundle_file_name = "secure_connect_bundle.zip"  # Replace with your desired file name
    download_file(bundle_url, bundle_file_name)
    full_path = download_file(bundle_url, bundle_file_name)

    # Connect to Astra DB
    cluster = Cluster(cloud={'secure_connect_bundle': full_path},
                    auth_provider=PlainTextAuthProvider(st.secrets["ASTRA_CLIENT_ID"],
                                                        st.secrets["ASTRA_CLIENT_SECRET"]))
    return cluster.connect()

# Cache OpenAI Embedding for future runs
@st.cache_resource(show_spinner=lang_dict['load_embedding'])
def load_embedding():
    print("load_embedding")
    # Get the OpenAI Embedding
    return OpenAIEmbeddings()


# Cache Vector Store for future runs
@st.cache_resource(show_spinner=lang_dict['load_vectorstore'])
def load_vectorstore(username):
    print("load_vectorstore")
    # Get the load_vectorstore store from Astra DB
    return Cassandra(
        embedding=embedding,
        session=session,
        keyspace='demo',
        table_name=f"vector_context_{username}"
    )


# Cache Retriever for future runs
@st.cache_resource(show_spinner=lang_dict['load_retriever'])
def load_retriever():
    print("load_retriever")
    # Get the Retriever from the Vectorstore
    return vectorstore.as_retriever(
        search_kwargs={"k": top_k_vectorstore}
    )


# Cache OpenAI Chat Model for future runs
@st.cache_resource(show_spinner=lang_dict['load_model'])
def load_model():
    print("load_model")
    # Get the OpenAI Chat Model
    return ChatOpenAI(
        temperature=0.3,
        model='gpt-4-1106-preview',
        streaming=True,
        verbose=True
    )


# Cache Chat History for future runs
@st.cache_resource(show_spinner=lang_dict['load_message_history'])
def load_chat_history(username):
    print("load_chat_history")
    return CassandraChatMessageHistory(
        session_id=username,
        session=session,
        keyspace='demo',
        ttl_seconds=864000  # Ten days
    )


@st.cache_resource(show_spinner=lang_dict['load_message_history'])
def load_memory():
    print("load_memory")
    return ConversationBufferWindowMemory(
        chat_memory=chat_history,
        return_messages=True,
        k=top_k_memory,
        memory_key="chat_history",
        input_key="question",
        output_key='answer',
    )


# Cache prompt
@st.cache_data()
def load_prompt():
    print("load_prompt")
    template = """You're a helpful AI assistent tasked to answer the user's questions.
You're friendly and you answer extensively with multiple sentences. You prefer to use bulletpoints to summarize.
If you don't know the answer, just say 'I do not know the answer'.

Use the following context to answer the question:
{context}

Use the previous chat history to answer the question:
{chat_history}

Question:
{question}

Answer in the user's language:"""

    return ChatPromptTemplate.from_messages([("system", template)])


#####################
### Session state ###
#####################

# Start with empty messages, stored in session state
if 'messages' not in st.session_state:
    st.session_state.messages = [AIMessage(content=lang_dict['assistant_welcome'])]

############
### Main ###
############

# Check for password
if not check_password():
    st.stop()  # Do not continue if check_password is not True.

with st.sidebar:
    st.image('./assets/datastax-logo.svg')
    st.text('')

"""
## Your personal effectivity booster
Generative AI is considered to bring the next Industrial Revolution.

Why? Studies show a **37% efficiency boost** in day to day work activities!

#### What is this app?
This app is a Chat Agent which takes into account Enterprise Context to provide meaningfull and contextual responses.
Why is this a big thing? It is because the underlying Foundational Large Language Models are not trained on Enterprise Data. They have no way of knowing anything about your organization.
Also they are trained upon a moment in time, so typically miss out on relevant and recent information.

#### What does it know?
The app has been preloaded with the following context:
- [PDF Factsheet NN România](https://www.nn.ro/sites/default/files/2023-05/nn_romania_factsheet_2023_ro.pdf)
- [PDF Prospectul Simplificat al Schemei de Pensii Facultative al Fondului de Pensii Facultative NN ACTIV](https://www.nn.ro/sites/default/files/2023-06/Prospectul%20simplificat%20al%20schemei%20de%20pensii%20facultative%20NN%20ACTIV%20in%20vigoare%20%28Iunie%202023%29.pdf)

This means you can start interacting with your personal assistant based on the above topics.

#### Adding additional context
On top of the above you have the opportunity to add additional information which then can be taken into account by the personal assistant. Just drop a PDF or Text file into the upload box in the sidebar and hit `Save`.

By the way... Be careful with the `Delete context` button. As this will do exactly that. I deletes the preloaded content mentioned above rendering the personal assistant non-contextual :)

---
"""

# Initialize
with st.sidebar:
    rails_dict = load_rails(username)
    session = load_session()
    embedding = load_embedding()
    vectorstore = load_vectorstore(username)
    retriever = load_retriever()
    model = load_model()
    chat_history = load_chat_history(username)
    memory = load_memory()
    prompt = load_prompt()

# Include the upload form for new data to be Vectorized
with st.sidebar:
    with st.form('upload'):
        uploaded_file = st.file_uploader(lang_dict['load_context'], type=['txt', 'pdf'], accept_multiple_files=True)
        submitted = st.form_submit_button(lang_dict['load_context_button'])
        if submitted:
            vectorize_text(uploaded_file)

# Drop the Conversational Memory
with st.sidebar:
    with st.form('delete_memory'):
        st.caption(lang_dict['delete_memory'])
        submitted = st.form_submit_button(lang_dict['delete_memory_button'])
        if submitted:
            with st.spinner(lang_dict['deleting_memory']):
                memory.clear()

# Drop the vector data and start from scratch
with st.sidebar:
    with st.form('delete_context'):
        st.caption(lang_dict['delete_context'])
        submitted = st.form_submit_button(lang_dict['delete_context_button'])
        if submitted:
            with st.spinner(lang_dict['deleting_context']):
                vectorstore.clear()
                memory.clear()
                st.session_state.clear()
                st.session_state.messages = [AIMessage(content=lang_dict['assistant_welcome'])]

# Draw rails
with st.sidebar:
    st.subheader(rails_dict[0])
    st.caption(rails_dict[1])
    for i in rails_dict:
        if i > 1:
            st.markdown(f"{i - 1}. {rails_dict[i]}")

# Draw all messages, both user and agent so far (every time the app reruns)
for message in st.session_state.messages:
    st.chat_message(message.type).markdown(message.content)

# Now get a prompt from a user
if question := st.chat_input(lang_dict['assistant_question']):
    print(f"Got question {question}")

    # Add the prompt to messages, stored in session state
    st.session_state.messages.append(HumanMessage(content=question))

    # Draw the prompt on the page
    print(f"Draw prompt")
    with st.chat_message('human'):
        st.markdown(question)

    # Get the results from Langchain
    print(f"Chat message")
    with st.chat_message('assistant'):
        # UI placeholder to start filling with agent response
        response_placeholder = st.empty()

        history = memory.load_memory_variables({})
        print(f"Using memory: {history}")

        inputs = RunnableMap({
            'context': lambda x: retriever.get_relevant_documents(x['question']),
            'chat_history': lambda x: x['chat_history'],
            'question': lambda x: x['question']
        })
        print(f"Using inputs: {inputs}")

        chain = inputs | prompt | model
        print(f"Using chain: {chain}")

        # Call the chain and stream the results into the UI
        callback = StreamHandler(response_placeholder)
        response = chain.invoke({'question': question, 'chat_history': history}, config={'callbacks': [callback]})
        print(f"Response: {response}")
        content = response.content

        # Write the sources used
        relevant_documents = retriever.get_relevant_documents(question)
        content += """

*The following context was used for this answer:*  
"""
        sources = []
        for doc in relevant_documents:
            source = doc.metadata['source']
            page_content = doc.page_content
            if source not in sources:
                content += f"""📙 :orange[{os.path.basename(os.path.normpath(source))}]  
"""
                sources.append(source)
        print(f"Used sources: {sources}")

        # Write the final answer without the cursor
        response_placeholder.markdown(content)

        # Add the result to memory
        memory.save_context({'question': question}, {'answer': content})

        # Add the answer to the messages session state
        st.session_state.messages.append(AIMessage(content=content))

with st.sidebar:
    st.caption("v11.07.01")
